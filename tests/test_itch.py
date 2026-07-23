import unittest
from decimal import Decimal

from bist_orderbook.domain import BookEvent, EventType, Side
from bist_orderbook.itch import BistechItchDecoder, InstrumentDirectory
from bist_orderbook.moldudp64 import MoldUDP64Packet


def put(buffer: bytearray, offset: int, length: int, value: int) -> None:
    buffer[offset : offset + length] = value.to_bytes(length, "big", signed=value < 0)


class BistechItchDecoderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.decoder = BistechItchDecoder()
        self.decoder.decode_message(b"T" + (1_777_249_784).to_bytes(4, "big"), 1)

    def directory_message(self) -> bytes:
        message = bytearray(130)
        message[0] = ord("R")
        put(message, 1, 4, 100)
        put(message, 5, 4, 42)
        message[9:41] = b"ASELS.E".ljust(32)
        message[41:73] = b"ASELSAN".ljust(32)
        message[73:85] = b"TRAASELS91H2"
        message[85] = 5
        message[86:89] = b"TRY"
        put(message, 89, 2, 2)
        put(message, 91, 2, 0)
        put(message, 129, 1, 1)
        return bytes(message)

    def test_decodes_directory_and_scaled_add_order(self) -> None:
        directory = self.decoder.decode_message(self.directory_message(), 2)
        self.assertIsInstance(directory, InstrumentDirectory)
        assert isinstance(directory, InstrumentDirectory)
        self.assertEqual(directory.symbol, "ASELS.E")
        self.assertEqual(directory.price_decimals, 2)

        message = bytearray(45)
        message[0] = ord("A")
        put(message, 1, 4, 200)
        put(message, 5, 8, 99)
        put(message, 13, 4, 42)
        message[17] = ord("B")
        put(message, 18, 4, 1)
        put(message, 22, 8, 1_000)
        put(message, 30, 4, 5_325)
        message[36] = 2
        put(message, 37, 8, 123)

        event = self.decoder.decode_message(bytes(message), 3)

        self.assertIsInstance(event, BookEvent)
        assert isinstance(event, BookEvent)
        self.assertEqual(event.event_type, EventType.ADD)
        self.assertEqual(event.side, Side.BUY)
        self.assertEqual(event.price, Decimal("53.25"))
        self.assertEqual(event.quantity, 1_000)

    def test_mold_sequence_increments_per_inner_message(self) -> None:
        packet = MoldUDP64Packet(
            session="TR29620650",
            sequence_number=10,
            messages=(b"T" + (1_777_249_784).to_bytes(4, "big"), self.directory_message()),
        )
        decoded = self.decoder.decode_packet(packet)
        self.assertEqual(decoded[0].sequence_number, 11)


if __name__ == "__main__":
    unittest.main()

