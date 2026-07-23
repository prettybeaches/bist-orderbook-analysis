import unittest

from bist_orderbook.moldudp64 import decode_moldudp64


class MoldUDP64Test(unittest.TestCase):
    def test_decodes_heartbeat(self) -> None:
        payload = b"TR29620650" + (1737).to_bytes(8, "big") + b"\x00\x00"
        packet = decode_moldudp64(payload)

        self.assertEqual(packet.session, "TR29620650")
        self.assertEqual(packet.sequence_number, 1737)
        self.assertTrue(packet.is_heartbeat)

    def test_decodes_multiple_messages(self) -> None:
        messages = (b"Sabc", b"Adefgh")
        body = b"".join(len(message).to_bytes(2, "big") + message for message in messages)
        payload = b"TR29620650" + (42).to_bytes(8, "big") + b"\x00\x02" + body

        packet = decode_moldudp64(payload)

        self.assertEqual(packet.messages, messages)

    def test_rejects_truncated_message(self) -> None:
        payload = b"TR29620650" + (42).to_bytes(8, "big") + b"\x00\x01\x00\x05abc"
        with self.assertRaisesRegex(ValueError, "truncated MoldUDP64 message body"):
            decode_moldudp64(payload)


if __name__ == "__main__":
    unittest.main()
