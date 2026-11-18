"""An IRS that gets ARQ_SESSION_OPEN with another protocol version answers with
the abort flag and ends the session. It used to set ABORTED without the
"finished" event the other aborts send, so the GUI kept showing the session
(the ARQ role widget stayed on IRS) and the modem stayed busy, as
ARQSession.__init__ calls setARQ(True)."""

import threading
import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.arq_session_irs import ARQSessionIRS, IRS_State


def make_irs():
    irs = ARQSessionIRS.__new__(ARQSessionIRS)
    modem = {"maximum_bandwidth": 2438, "enable_fsk": True, "enable_high_rate": True}
    irs.ctx = types.SimpleNamespace(
        config_manager=types.SimpleNamespace(config={"MODEM": modem}),
        state_manager=mock.Mock(channel_busy_slot=[False] * 5),
        event_manager=mock.Mock(),
    )
    irs.id, irs.dxcall, irs.version, irs.protocol_version, irs.snr = 9, "AA1AAA-1", 2, 2, 10
    irs.abort = False
    irs.fsk = False
    irs.state = IRS_State.NEW
    irs.burst_ack_lock = threading.Lock()
    irs.received_bytes = irs.total_length = 0
    irs.log = mock.Mock()
    irs.frame_factory = mock.Mock()
    irs.launch_transmit_and_wait = mock.Mock()
    irs.calculate_session_statistics = mock.Mock(return_value={"total_bytes": 0})
    return irs


class TestIrsProtocolMismatch(unittest.TestCase):
    def test_mismatch_ends_the_session(self):
        irs = make_irs()
        irs.send_open_ack({"maximum_bandwidth": 2438, "protocol_version": 1})

        self.assertIs(irs.state, IRS_State.ABORTED)
        self.assertTrue(irs.frame_factory.build_arq_session_open_ack.call_args.kwargs["flag_abort"])
        irs.launch_transmit_and_wait.assert_called_once()
        irs.ctx.event_manager.send_arq_session_finished.assert_called_once_with(
            False, 9, "AA1AAA-1", False, "ABORTED", statistics={"total_bytes": 0}
        )
        irs.ctx.state_manager.setARQ.assert_called_once_with(False)

    def test_matching_version_goes_on(self):
        irs = make_irs()
        irs.send_open_ack({"maximum_bandwidth": 2438, "protocol_version": 2})

        self.assertIs(irs.state, IRS_State.OPEN_ACK_SENT)
        self.assertFalse(irs.frame_factory.build_arq_session_open_ack.call_args.kwargs["flag_abort"])
        irs.ctx.event_manager.send_arq_session_finished.assert_not_called()
        irs.ctx.state_manager.setARQ.assert_not_called()


if __name__ == "__main__":
    unittest.main()
