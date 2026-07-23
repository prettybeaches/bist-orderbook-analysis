import sqlite3
import tempfile
import unittest
from pathlib import Path

from bist_orderbook.capture import UDPDatagram
from bist_orderbook.ingestion import ReplayProcessor, SelectedInstrument
from bist_orderbook.storage import SQLiteStore


def mold(sequence: int, messages: tuple[bytes, ...]) -> bytes:
    body = b"".join(len(message).to_bytes(2, "big") + message for message in messages)
    return b"TR29620650" + sequence.to_bytes(8, "big") + len(messages).to_bytes(2, "big") + body


def add_order(book_id: int) -> bytes:
    message = bytearray(45)
    message[0] = ord("A")
    message[1:5] = (123_456_789).to_bytes(4, "big")
    message[5:13] = (99).to_bytes(8, "big")
    message[13:17] = book_id.to_bytes(4, "big")
    message[17] = ord("B")
    message[18:22] = (1).to_bytes(4, "big")
    message[22:30] = (1_000).to_bytes(8, "big")
    message[30:34] = (5_325).to_bytes(4, "big", signed=True)
    message[36] = 2
    message[37:45] = (123_456_789).to_bytes(8, "big")
    return bytes(message)


class ReplayProcessorTest(unittest.TestCase):
    def test_replays_selected_multicast_order_into_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "orderbook.db"
            processor = ReplayProcessor(
                (
                    SelectedInstrument(
                        order_book_id=42,
                        symbol="ASELS.E",
                        market="EQUITY",
                        price_decimals=2,
                    ),
                ),
                SQLiteStore(path),
                batch_size=10,
            )
            seconds = b"T" + (1_777_249_784).to_bytes(4, "big")
            payload = mold(1, (seconds, add_order(42)))
            processor.process(
                UDPDatagram("10.0.0.1", "233.1.1.1", 40_000, 21_001, payload)
            )
            processor.flush()

            with sqlite3.connect(path) as connection:
                snapshot = connection.execute(
                    "SELECT captured_at_ns, sequence_number FROM snapshots"
                ).fetchone()
                level = connection.execute(
                    "SELECT side, level, price, quantity FROM price_levels"
                ).fetchone()

        self.assertEqual(snapshot, (1_777_249_784_123_456_789, 2))
        self.assertEqual(level, ("B", 1, "53.25", 1_000))
        self.assertEqual(processor.stats.snapshots_written, 1)

    def test_ignores_unicast_recovery_traffic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            processor = ReplayProcessor(
                (SelectedInstrument(42, "ASELS.E", "EQUITY", 2),),
                SQLiteStore(Path(temporary_directory) / "orderbook.db"),
            )
            seconds = b"T" + (1_777_249_784).to_bytes(4, "big")
            processor.process(
                UDPDatagram(
                    "10.0.0.1",
                    "10.0.0.2",
                    24_001,
                    40_000,
                    mold(1, (seconds, add_order(42))),
                )
            )
            processor.flush()

        self.assertEqual(processor.stats.selected_messages, 0)
        self.assertEqual(processor.stats.snapshots_written, 0)


if __name__ == "__main__":
    unittest.main()
