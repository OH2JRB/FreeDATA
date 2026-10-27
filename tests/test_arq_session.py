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

    def channelWorker(self, ctx_a, ctx_b):
        while self.channels_running:
            try:
                # Station A gets the data from its transmit queue
                transmission = ctx_a.TESTMODE_TRANSMIT_QUEUE.get(timeout=1)
                print(f"Station A sending: {transmission[1]}", len(transmission[1]), transmission[0])

                transmission[1] += bytes(2)  # 2bytes crc simulation

                if random.randint(0, 100) < self.loss_probability:
                    self.logger.info(f"[{threading.current_thread().name}] Frame lost...")
                    continue

                # Forward data from Station A to Station B's receive queue
                if ctx_b:
                    ctx_b.TESTMODE_RECEIVE_QUEUE.put(transmission)
                    self.logger.info("Data forwarded to Station B")

                frame_bytes = transmission[1]
                if len(frame_bytes) == 5:
                    mode_name = "SIGNALLING_ACK"
                else:
                    mode_name = transmission[0]

                snr = 15
                ctx_b.service_manager.frame_dispatcher.process_data(
                    frame_bytes, None, len(frame_bytes), snr, 0, mode_name=mode_name
                )

            except queue.Empty:
                continue

        self.logger.info(f"[{threading.current_thread().name}] Channel closed.")

    def waitForSession(self, q, outbound=False):
        key = "arq-transfer-outbound" if outbound else "arq-transfer-inbound"
        while True and self.channels_running:
            ev = q.get()
            if key in ev and ("success" in ev[key] or "ABORTED" in ev[key]):
                self.logger.info(f"[{threading.current_thread().name}] {key} session ended.")
                break

    def establishChannels(self):
        self.channels_running = True
        self.channelA = threading.Thread(target=self.channelWorker, args=[self.ctx_ISS, self.ctx_IRS], name="channelA")
        self.channelA.start()

        self.channelB = threading.Thread(target=self.channelWorker, args=[self.ctx_IRS, self.ctx_ISS], name="channelB")
        self.channelB.start()

    def waitAndCloseChannels(self):
        self.waitForSession(self.ctx_ISS.TESTMODE_EVENTS, True)
        self.channels_running = False
        self.waitForSession(self.ctx_IRS.TESTMODE_EVENTS, False)
        self.channels_running = False

    def DisabledtestARQSessionSmallPayload(self):
        # set Packet Error Rate (PER) / frame loss probability
        self.loss_probability = 30

        self.establishChannels()
        params = {
            "dxcall": "AA1AAA-1",
            "data": base64.b64encode(bytes("Hello world!", encoding="utf-8")),
            "type": "raw_lzma",
        }
        cmd = ARQRawCommand(self.ctx_ISS, params)
        cmd.run()
        self.waitAndCloseChannels()
        print(self.ctx_ISS.TESTMODE_EVENTS.empty())

        while not self.ctx_ISS.TESTMODE_EVENTS.empty():
            event = self.ctx_ISS.TESTMODE_EVENTS.get()
            success = event.get("arq-transfer-outbound", {}).get("success", None)
            if success is not None:
                self.assertTrue(success, f"Test failed because of wrong success: {success}")

        # self.ctx_IRS.shutdown()
        # self.ctx_ISS.shutdown()

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

    def DisabledtestSessionCleanupISS(self):
        params = {
            "dxcall": "AA1AAA-1",
            "data": base64.b64encode(np.random.bytes(100)),
        }
        cmd = ARQRawCommand(self.config, self.iss_state_manager, self.iss_event_queue, params)
        cmd.run(self.iss_event_queue, self.iss_modem)
        for session_id in self.iss_state_manager.arq_iss_sessions:
            session = self.iss_state_manager.arq_iss_sessions[session_id]
            ISS_States = session.state_enum
            session.state = ISS_States.FAILED
            session.session_ended = time.time() - 1000
            if session.is_session_outdated():
                self.logger.info(f"session [{session_id}] outdated - deleting it")
                self.iss_state_manager.remove_arq_iss_session(session_id)
                break
        del cmd

    def DisabledtestSessionCleanupIRS(self):
        session = arq_session_irs.ARQSessionIRS(
            self.config, self.irs_modem, "AA1AAA-1", random.randint(0, 255), self.irs_state_manager
        )
        self.irs_state_manager.register_arq_irs_session(session)
        for session_id in self.irs_state_manager.arq_irs_sessions:
            session = self.irs_state_manager.arq_irs_sessions[session_id]
            irs_States = session.state_enum
            session.state = irs_States.FAILED
            session.session_ended = time.time() - 1000
            if session.is_session_outdated():
                self.logger.info(f"session [{session_id}] outdated - deleting it")
                self.irs_state_manager.remove_arq_irs_session(session_id)
                break


if __name__ == "__main__":
    unittest.main()
