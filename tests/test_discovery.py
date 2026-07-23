import csv
import tempfile
import unittest
from pathlib import Path

from bist_orderbook.capture import UDPDatagram
from bist_orderbook.discovery import InstrumentDiscovery, market_name, write_instruments_csv


def mold(sequence: int, messages: tuple[bytes, ...]) -> bytes:
    body = b"".join(len(message).to_bytes(2, "big") + message for message in messages)
    return b"TR29620650" + sequence.to_bytes(8, "big") + len(messages).to_bytes(2, "big") + body


def directory(
    book_id: int, symbol: str, product: int = 5, underlying_book_id: int = 0
) -> bytes:
    message = bytearray(130)
    message[0] = ord("R")
    message[1:5] = (100).to_bytes(4, "big")
    message[5:9] = book_id.to_bytes(4, "big")
    message[9:41] = symbol.encode("latin-1").ljust(32)
    message[41:73] = symbol.encode("latin-1").ljust(32)
    message[73:85] = b"TESTISIN0001"
    message[85] = product
    message[86:89] = b"TRY"
    message[89:91] = (2).to_bytes(2, "big")
    message[114:118] = underlying_book_id.to_bytes(4, "big")
    message[129] = 1
    return bytes(message)


def datagram(payload: bytes, destination_port: int = 21001) -> UDPDatagram:
    return UDPDatagram("10.0.0.1", "233.1.1.1", 24001, destination_port, payload)


class InstrumentDiscoveryTest(unittest.TestCase):
    def test_discovers_and_deduplicates_directories(self) -> None:
        discovery = InstrumentDiscovery()
        seconds = b"T" + (1_777_249_784).to_bytes(4, "big")
        discovery.process(datagram(mold(1, (seconds, directory(42, "ASELS.E")))))
        discovery.process(datagram(mold(3, (directory(42, "ASELS.E"),))))

        self.assertEqual(len(discovery.instruments), 1)
        self.assertEqual(discovery.instruments[42].symbol, "ASELS.E")
        self.assertEqual(discovery.stats.sequence_gaps, 0)

    def test_tracks_sequence_gaps_per_channel(self) -> None:
        discovery = InstrumentDiscovery()
        seconds = b"T" + (1_777_249_784).to_bytes(4, "big")
        discovery.process(datagram(mold(1, (seconds,))))
        discovery.process(datagram(mold(4, (directory(42, "ASELS.E"),))))

        self.assertEqual(discovery.stats.sequence_gaps, 1)
        self.assertEqual(discovery.stats.missing_messages, 2)

    def test_writes_resolved_underlying_symbol(self) -> None:
        seconds = b"T" + (1_777_249_784).to_bytes(4, "big")
        discovery = InstrumentDiscovery()
        equity = directory(42, "ASELS.E", underlying_book_id=6_000)
        future = directory(84, "F_ASELS0726", product=3, underlying_book_id=6_000)
        discovery.process(datagram(mold(1, (seconds, equity, future))))

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "instruments.csv"
            items = tuple(discovery.instruments.values())
            write_instruments_csv(path, items)
            with path.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))

        future_row = next(row for row in rows if row["symbol"] == "F_ASELS0726")
        self.assertEqual(future_row["underlying_symbol"], "ASELS.E")
        self.assertEqual(market_name(3), "FUTURE")


if __name__ == "__main__":
    unittest.main()
