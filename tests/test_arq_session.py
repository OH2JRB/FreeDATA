import time

import unittest
import unittest.mock
import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.context import AppContext

import queue
import threading
import base64
from freedata_server.command_arq_raw import ARQRawCommand
import random
import structlog
import numpy as np
from freedata_server import arq_session_irs
from freedata_server.arq_data_type_handler import ARQ_SESSION_TYPES
from freedata_server.arq_session_iss import ARQSessionISS


class DummyCtx:
    def __init__(self):
        pass


class TestARQSession(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.logger = structlog.get_logger("TESTS")

        # ISS

        cls.ctx_ISS = AppContext(testenv.config_path())
        cls.ctx_ISS.TESTMODE = True
        cls.ctx_ISS.startup()

        # IRS
        cls.ctx_IRS = AppContext(testenv.config_path())
        cls.ctx_IRS.TESTMODE = True
        cls.ctx_IRS.startup()

        # simulate a busy condition
        cls.ctx_IRS.state_manager.channel_busy_slot = [True, False, False, False, False]
        # Frame loss probability in %
        cls.loss_probability = 0

        cls.channels_running = False

    @classmethod
    def tearDownClass(cls):
        # Clean shutdown after all tests
        cls.ctx_IRS.shutdown()
        cls.ctx_ISS.shutdown()

    def channelWorker(self, ctx_a, ctx_b, seed=None):
        # each channel loses frames from its own random sequence
        rng = random.Random(seed)
        while self.channels_running:
            try:
                # Station A gets the data from its transmit queue
                mode, frames = ctx_a.TESTMODE_TRANSMIT_QUEUE.get(timeout=1)
            except queue.Empty:
                continue

            # a burst is a list of frames sent in one transmission; each is lost on its own
            for frame in frames if isinstance(frames, list) else [frames]:
                # a copy: the sender keeps the frame for its retries
                frame_bytes = bytes(frame) + bytes(2)  # 2bytes crc simulation

                if rng.randint(0, 99) < self.loss_probability:
                    self.logger.info(f"[{threading.current_thread().name}] Frame lost...")
                    continue

                # signalling_ack frames carry no type byte; the dispatcher knows
                # them by the mode they were received in
                mode_name = "SIGNALLING_ACK" if len(frame_bytes) == 5 else mode

                snr = 15
                ctx_b.service_manager.frame_dispatcher.process_data(
                    frame_bytes, None, len(frame_bytes), snr, 0, mode_name=mode_name
                )

        self.logger.info(f"[{threading.current_thread().name}] Channel closed.")

    def waitForSession(self, q, outbound=False):
        key = "arq-transfer-outbound" if outbound else "arq-transfer-inbound"
        while True and self.channels_running:
            ev = q.get()
            if key in ev and ("success" in ev[key] or "ABORTED" in ev[key]):
                self.logger.info(f"[{threading.current_thread().name}] {key} session ended.")
                break

    def establishChannels(self, seed=None):
        self.channels_running = True
        self.channelA = threading.Thread(
            target=self.channelWorker, args=[self.ctx_ISS, self.ctx_IRS, seed], name="channelA"
        )
        self.channelA.start()

        self.channelB = threading.Thread(
            target=self.channelWorker,
            args=[self.ctx_IRS, self.ctx_ISS, None if seed is None else seed + 1],
            name="channelB",
        )
        self.channelB.start()

    def wait_for_finished(self, ctx, key, timeout):
        """The first arq-transfer event of the session's end (it has "success"), or None."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                ev = ctx.TESTMODE_EVENTS.get(timeout=0.5)
            except queue.Empty:
                continue
            if "success" in ev.get(key, {}):
                return ev[key]
        return None

    def waitAndCloseChannels(self):
        self.waitForSession(self.ctx_ISS.TESTMODE_EVENTS, True)
        self.channels_running = False
        self.waitForSession(self.ctx_IRS.TESTMODE_EVENTS, False)
        self.channels_running = False

    def testARQSessionSmallPayloadFrameLoss(self):
        # 30 % of the frames are lost both ways: the retries, and the FSK fallback
        # after unanswered frames, still get the data through
        self.loss_probability = 30
        payload = np.random.bytes(600)
        # events of earlier tests must not pass for this session's end
        for ctx in (self.ctx_ISS, self.ctx_IRS):
            while not ctx.TESTMODE_EVENTS.empty():
                ctx.TESTMODE_EVENTS.get_nowait()

        self.establishChannels(seed=1)
        try:
            params = {"dxcall": "AA1AAA-1", "data": base64.b64encode(payload), "type": "raw"}
            ARQRawCommand(self.ctx_ISS, params).run()
            inbound = self.wait_for_finished(self.ctx_IRS, "arq-transfer-inbound", 300)
            outbound = self.wait_for_finished(self.ctx_ISS, "arq-transfer-outbound", 120)
        finally:
            self.loss_probability = 0
            self.channels_running = False
            self.channelA.join(5)
            self.channelB.join(5)

        self.assertIsNotNone(inbound, "the IRS never finished the session")
        self.assertTrue(inbound["success"], inbound)
        self.assertEqual(base64.b64decode(inbound["data"]), payload)
        self.assertIsNotNone(outbound, "the ISS never finished the session")
        self.assertTrue(outbound["success"], outbound)

    def testARQSessionLargePayload(self):
        # set Packet Error Rate (PER) / frame loss probability
        self.loss_probability = 0

        self.establishChannels()
        params = {
            "dxcall": "AA1AAA-1",
            "data": base64.b64encode(np.random.bytes(1000)),
            "type": "raw_lzma",
        }
        cmd = ARQRawCommand(self.ctx_ISS, params)
        cmd.run()

        self.waitAndCloseChannels()
        # del cmd
        print(self.ctx_ISS.TESTMODE_EVENTS.empty())

        while not self.ctx_ISS.TESTMODE_EVENTS.empty():
            event = self.ctx_ISS.TESTMODE_EVENTS.get()
            success = event.get("arq-transfer-outbound", {}).get("success", None)
            if success is not None:
                self.assertTrue(success, f"Test failed because of wrong success: {success}")

        # self.ctx_IRS.shutdown()
        # self.ctx_ISS.shutdown()

    def testARQSessionIRSSuccessEmitsFinishedEvent(self):
        # Gap 3 regression test: a completed IRS session must emit exactly
        # one "arq-transfer-inbound" event with success=True, and the
        # payload must round-trip through the event's base64 data field
        # (previously the success branch in arq_session_irs.py never called
        # send_arq_session_finished at all, so no such event was ever
        # emitted -- only the abort/failure paths did).
        self.loss_probability = 0
        payload = np.random.bytes(200)

        self.establishChannels()
        params = {
            "dxcall": "AA1AAA-1",
            "data": base64.b64encode(payload),
            # "raw" (not "raw_lzma"): the event's data field carries the
            # bytes as received over the air, before ARQDataTypeHandler's
            # separate decompression step -- "raw" keeps this an honest
            # exact-match round-trip check instead of asserting equality
            # against still-compressed bytes.
            "type": "raw",
        }
        cmd = ARQRawCommand(self.ctx_ISS, params)
        cmd.run()
        self.waitForSession(self.ctx_ISS.TESTMODE_EVENTS, True)
        self.channels_running = False

        finished_events = []
        while not self.ctx_IRS.TESTMODE_EVENTS.empty():
            ev = self.ctx_IRS.TESTMODE_EVENTS.get()
            if "arq-transfer-inbound" in ev:
                finished_events.append(ev["arq-transfer-inbound"])

        successes = [e for e in finished_events if e.get("success") is True]
        self.assertEqual(
            len(successes),
            1,
            f"expected exactly one successful arq-transfer-inbound event, got {finished_events}",
        )
        received = base64.b64decode(successes[0]["data"])
        self.assertEqual(received, payload)

    def start_transfer_and_wait_for_data(self):
        """Starts a transfer and returns its ISS and IRS sessions once data flows."""
        self.loss_probability = 0
        self.establishChannels()
        params = {
            "dxcall": "AA1AAA-1",
            "data": base64.b64encode(np.random.bytes(20000)),
        }
        earlier = set(self.ctx_ISS.state_manager.arq_iss_sessions)
        ARQRawCommand(self.ctx_ISS, params).run()
        deadline = time.time() + 60
        while time.time() < deadline:
            new = [s for i, s in list(self.ctx_ISS.state_manager.arq_iss_sessions.items()) if i not in earlier]
            iss = new[0] if new else None
            irs = self.ctx_IRS.state_manager.arq_irs_sessions.get(iss.id) if iss else None
            if iss and irs and iss.state is iss.state_enum.BURST_SENT:
                return iss, irs
            time.sleep(0.05)
        self.fail("the transfer did not start")

    def wait_for_state(self, session, state, timeout):
        deadline = time.time() + timeout
        while session.state is not state and time.time() < deadline:
            time.sleep(0.05)
        return session.state

    def stop_channels(self, iss, irs):
        """Ends the abort tests: the sessions and their events must not reach the
        tests that follow."""
        for session in (iss, irs):
            if session.state is not session.state_enum.ABORTED:
                session.transmission_aborted()
        self.channels_running = False
        self.channelA.join(5)
        self.channelB.join(5)
        self.ctx_ISS.state_manager.remove_arq_iss_session(iss.id)
        self.ctx_IRS.state_manager.remove_arq_irs_session(irs.id)
        self.ctx_ISS.state_manager.setARQ(False)
        self.ctx_IRS.state_manager.setARQ(False)
        for ctx in (self.ctx_ISS, self.ctx_IRS):
            for q in (ctx.TESTMODE_EVENTS, ctx.TESTMODE_TRANSMIT_QUEUE, ctx.TESTMODE_RECEIVE_QUEUE):
                while not q.empty():
                    q.get_nowait()

    def testARQSessionAbortTransmissionISS(self):
        # the user stops the transfer: the ISS tells the IRS with ARQ_STOP, and
        # the ARQ_STOP_ACK ends the abort long before the stop retries run out
        iss, irs = self.start_transfer_and_wait_for_data()
        try:
            started = time.time()
            self.ctx_ISS.state_manager.stop_transmission()
            self.assertLess(time.time() - started, 2)  # the API is not held up
            self.assertEqual(iss.state, iss.state_enum.ABORTING)
            self.assertTrue(self.ctx_ISS.state_manager.getARQ())  # busy while stopping

            self.assertEqual(self.wait_for_state(irs, irs.state_enum.ABORTED, 30), irs.state_enum.ABORTED)
            self.assertEqual(self.wait_for_state(iss, iss.state_enum.ABORTED, 30), iss.state_enum.ABORTED)
            retries_exhausted = iss.RETRIES_STOP * iss.TIMEOUT_STOP_ACK
            self.assertLess(time.time() - started, retries_exhausted)
            # transmission_aborted() sets ABORTED, then sends its event and clears
            # the ARQ flag: wait for it instead of reading it at once
            deadline = time.time() + 2
            while self.ctx_ISS.state_manager.getARQ() and time.time() < deadline:
                time.sleep(0.05)
            self.assertFalse(self.ctx_ISS.state_manager.getARQ())
        finally:
            self.stop_channels(iss, irs)

    def testARQSessionAbortTransmissionIRS(self):
        # the receiving side aborts: its next ACK carries the abort flag
        iss, irs = self.start_transfer_and_wait_for_data()
        try:
            irs.abort_transmission()
            self.assertEqual(self.wait_for_state(iss, iss.state_enum.ABORTED, 60), iss.state_enum.ABORTED)
        finally:
            self.stop_channels(iss, irs)

    def check_session_cleanup(self, sessions, make_session, running_state, irs):
        """check_if_running_arq_session() removes sessions that ended long ago
        (is_session_outdated) and still sees the running one."""
        saved = dict(sessions)
        sessions.clear()
        try:
            outdated = []
            for state in ("FAILED", "ENDED", "ABORTED"):
                session = make_session()
                session.state = session.state_enum[state]
                session.session_ended = time.time() - session.session_max_age - 10
                outdated.append(session)
            recent = make_session()
            recent.state = recent.state_enum.FAILED
            recent.session_ended = time.time()
            for session in outdated + [recent]:
                sessions[session.id] = session

            # only finished sessions: none is running, the old ones are gone
            self.assertFalse(self.ctx_ISS.state_manager.check_if_running_arq_session(irs=irs))
            self.assertEqual(list(sessions), [recent.id])

            running = make_session()
            running.state = running.state_enum[running_state]
            sessions[running.id] = running
            self.assertTrue(self.ctx_ISS.state_manager.check_if_running_arq_session(irs=irs))
        finally:
            sessions.clear()
            sessions.update(saved)

    def testSessionCleanupISS(self):
        ids = iter(range(200, 256))
        state_manager = self.ctx_ISS.state_manager

        def make_session():
            session = ARQSessionISS(self.ctx_ISS, "AA1AAA-1", bytes(100), ARQ_SESSION_TYPES.raw.value)
            session.id = next(ids)
            return session

        self.check_session_cleanup(state_manager.arq_iss_sessions, make_session, "BURST_SENT", irs=False)

    def testSessionCleanupIRS(self):
        ids = iter(range(200, 256))
        state_manager = self.ctx_ISS.state_manager

        def make_session():
            return arq_session_irs.ARQSessionIRS(self.ctx_ISS, "AA1AAA-1", next(ids))

        self.check_session_cleanup(state_manager.arq_irs_sessions, make_session, "BURST_REPLY_SENT", irs=True)


if __name__ == "__main__":
    unittest.main()
