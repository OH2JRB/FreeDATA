"""The IRS answers an ARQ_STOP with an ARQ_STOP_ACK the ISS can read: in the
signalling mode, since signalling_ack frames carry no frame type and are read
as ARQ_BURST_ACKs (see freedata_server/docs/protocol-frames.md, section 3.1)."""

import threading
import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import codec2
from freedata_server.arq_session_irs import ARQSessionIRS, IRS_State
from freedata_server.config import CONFIG
from freedata_server.data_frame_factory import DataFrameFactory
from freedata_server.modem_frametypes import FRAME_TYPE

FREEDV_MODE = codec2.FREEDV_MODE
SESSION_ID = 42


def make_factory():
    ctx = types.SimpleNamespace()
    ctx.config_manager = CONFIG(ctx, testenv.config_path())
    return DataFrameFactory(ctx)


def make_irs(fsk=False):
    """An IRS with only what send_stop_ack needs, without a modem."""
    irs = ARQSessionIRS.__new__(ARQSessionIRS)
    irs.id = SESSION_ID
    irs.dxcall = "AA1AAA-1"
    irs.fsk = fsk
    irs.state = IRS_State.BURST_REPLY_SENT
    irs.received_bytes = 0
    irs.total_length = 100
    irs.log = mock.Mock()
    irs.burst_ack_lock = threading.RLock()
    irs.frame_factory = make_factory()
    irs.launch_transmit_and_wait = mock.Mock()
    irs.calculate_session_statistics = mock.Mock(return_value={})
    irs.ctx = types.SimpleNamespace(
        state_manager=mock.Mock(),
        event_manager=mock.Mock(),
        config_manager=types.SimpleNamespace(config={"STATION": {"enable_stats": False}}),
    )
    return irs


class TestStopAck(unittest.TestCase):
    def test_sent_in_signalling_mode(self):
        irs = make_irs()
        irs.send_stop_ack({"session_id": SESSION_ID})
        frame, _timeout = irs.launch_transmit_and_wait.call_args.args[:2]
        self.assertEqual(irs.launch_transmit_and_wait.call_args.kwargs["mode"], FREEDV_MODE.signalling)
        self.assertEqual(frame[0], FRAME_TYPE.ARQ_STOP_ACK.value)
        self.assertEqual(irs.state, IRS_State.ABORTED)

    def test_sent_in_fsk_after_the_fallback(self):
        irs = make_irs(fsk=True)
        irs.send_stop_ack({"session_id": SESSION_ID})
        frames = irs.launch_transmit_and_wait.call_args.args[0]
        self.assertEqual(irs.launch_transmit_and_wait.call_args.kwargs["mode"], ARQSessionIRS.FSK_MODE)
        self.assertEqual(len(frames), ARQSessionIRS.FSK_SIGNALLING_COPIES)

    def test_iss_reads_it_as_stop_ack(self):
        factory = make_factory()
        stop_ack = factory.build_arq_stop_ack(SESSION_ID)
        frame = factory.deconstruct(stop_ack, mode_name="SIGNALLING")
        self.assertEqual(frame["frame_type"], "ARQ_STOP_ACK")
        self.assertEqual(frame["session_id"], SESSION_ID)

    def test_signalling_ack_would_lose_it(self):
        # what went wrong before: the first 3 bytes, read as a burst ACK of
        # the session whose id is the frame type
        factory = make_factory()
        stop_ack = factory.build_arq_stop_ack(SESSION_ID)
        frame = factory.deconstruct(stop_ack[:3], mode_name="SIGNALLING_ACK")
        self.assertEqual(frame["frame_type"], "ARQ_BURST_ACK")
        self.assertEqual(frame["session_id"], FRAME_TYPE.ARQ_STOP_ACK.value)


if __name__ == "__main__":
    unittest.main()
