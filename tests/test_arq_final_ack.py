"""A final burst ACK with a matching checksum ends the ISS's session even when
the ISS expected fewer bytes confirmed: an OFDM ACK has no offset, and a final
ACK the IRS repeats may answer a burst resent from an earlier offset (the
first final ACK lost, then the fallback speed level)."""

import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from test_arq_abort_race import make_iss


def final_ack(checksum):
    # as deconstruct() reads a signalling_ack burst ACK: no offset
    return {"flag": {"ABORT": False, "FINAL": True, "CHECKSUM": checksum}, "speed_level": 0}


class TestFinalAck(unittest.TestCase):
    def make_iss(self, expected_byte_offset):
        iss = make_iss()
        iss.confirmed_bytes = 129
        iss.expected_byte_offset = expected_byte_offset
        iss.transmission_ended = mock.Mock()
        iss.transmission_failed = mock.Mock()
        return iss

    def test_repeated_final_ack_after_resending(self):
        iss = self.make_iss(expected_byte_offset=174)
        iss.send_data(final_ack(checksum=True))
        iss.transmission_ended.assert_called_once()
        iss.transmission_failed.assert_not_called()
        self.assertEqual(iss.confirmed_bytes, iss.total_length)

    def test_final_ack_answering_the_last_burst(self):
        iss = self.make_iss(expected_byte_offset=1000)
        iss.send_data(final_ack(checksum=True))
        iss.transmission_ended.assert_called_once()

    def test_final_ack_with_wrong_checksum_fails(self):
        iss = self.make_iss(expected_byte_offset=1000)
        iss.send_data(final_ack(checksum=False))
        iss.transmission_failed.assert_called_once()
        iss.transmission_ended.assert_not_called()


if __name__ == "__main__":
    unittest.main()
