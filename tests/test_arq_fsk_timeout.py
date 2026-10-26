"""The ISS waits for the IRS's answer longer after a frame in an FSK mode: codec2
decodes an FSK frame up to one frame length after its end, 5.44 s for
data_fsk4_50, longer than TIMEOUT_TRANSFER (see
freedata_server/docs/fading-channels.md, section 8)."""

import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import codec2
from freedata_server.arq_session_iss import ARQSessionISS, ISS_State

FREEDV_MODE = codec2.FREEDV_MODE


def make_iss(speed_level=0):
    """An ISS with only what transmit_wait_and_retry needs, without a modem."""
    iss = ARQSessionISS.__new__(ARQSessionISS)
    iss.speed_level = speed_level
    iss.state = ISS_State.INFO_SENT
    iss.log = mock.Mock()
    iss.transmit_frame = mock.Mock()
    return iss


class TestDecodeDelay(unittest.TestCase):
    def test_ofdm_modes_decode_at_once(self):
        for mode in (
            FREEDV_MODE.signalling,
            FREEDV_MODE.signalling_ack,
            FREEDV_MODE.datac4,
            FREEDV_MODE.data_ofdm_2438,
        ):
            self.assertEqual(codec2.get_decode_delay(mode.value), 0.0, mode.name)

    def test_fsk_modes_decode_a_frame_later(self):
        # measured: up to 5.1 s for data_fsk4_50 and 1.3 s for data_vhf_1
        self.assertAlmostEqual(codec2.get_decode_delay(FREEDV_MODE.data_fsk4_50.value), 5.44)
        self.assertAlmostEqual(codec2.get_decode_delay(FREEDV_MODE.data_vhf_1.value), 1.36)


class TestResponseTimeout(unittest.TestCase):
    def test_ofdm_timeouts_unchanged(self):
        iss = make_iss()
        self.assertEqual(iss.get_response_timeout(ARQSessionISS.TIMEOUT_TRANSFER), ARQSessionISS.TIMEOUT_TRANSFER)
        self.assertEqual(
            iss.get_response_timeout(ARQSessionISS.TIMEOUT_CONNECT_ACK, FREEDV_MODE.signalling),
            ARQSessionISS.TIMEOUT_CONNECT_ACK,
        )

    def test_fsk_speed_level_waits_longer(self):
        iss = make_iss()
        levels = {0: {"mode": FREEDV_MODE.data_fsk4_50}}
        with mock.patch.dict(ARQSessionISS.SPEED_LEVEL_DICT, levels):
            timeout = iss.get_response_timeout(ARQSessionISS.TIMEOUT_TRANSFER)
        self.assertAlmostEqual(timeout, ARQSessionISS.TIMEOUT_TRANSFER + 5.44)
        self.assertAlmostEqual(
            iss.get_response_timeout(ARQSessionISS.TIMEOUT_CONNECT_ACK, FREEDV_MODE.data_fsk4_50),
            ARQSessionISS.TIMEOUT_CONNECT_ACK + 5.44,
        )

    def test_transmit_wait_and_retry_uses_it(self):
        iss = make_iss()
        event = mock.Mock()
        event.wait.return_value = True  # answered
        with mock.patch("freedata_server.arq_session_iss.threading.Event", return_value=event):
            iss.transmit_wait_and_retry(b"frame", ARQSessionISS.TIMEOUT_CONNECT_ACK, 1, FREEDV_MODE.data_fsk4_50)
        iss.transmit_frame.assert_called_once_with(b"frame", FREEDV_MODE.data_fsk4_50)
        event.wait.assert_called_once()
        self.assertAlmostEqual(event.wait.call_args.args[0], ARQSessionISS.TIMEOUT_CONNECT_ACK + 5.44)


if __name__ == "__main__":
    unittest.main()
