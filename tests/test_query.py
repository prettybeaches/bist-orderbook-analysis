import csv
import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bist_orderbook.domain import BookSnapshot, PriceLevel, Side
from bist_orderbook.query import (
    SnapshotQuery,
    format_snapshots,
    parse_time_ns,
    query_snapshots,
    write_snapshot_csv,
)
from bist_orderbook.storage import SQLiteStore


class SnapshotQueryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "orderbook.db"
        self.store = SQLiteStore(self.database)
        self.store.upsert_instruments(((42, "ASELS.E", "EQUITY", None, None),))
        self.snapshots = [
            BookSnapshot(
                timestamp=datetime(2026, 4, 27, 7, 0, tzinfo=UTC),
                timestamp_ns=1_777_273_200_000_000_123,
                sequence_number=100,
                order_book_id=42,
                symbol="ASELS.E",
                levels=(PriceLevel(1, Side.BUY, Decimal("53.25"), 100, 2),),
            ),
            BookSnapshot(
                timestamp=datetime(2026, 4, 27, 7, 1, tzinfo=UTC),
                timestamp_ns=1_777_273_260_000_000_456,
                sequence_number=200,
                order_book_id=42,
                symbol="ASELS.E",
                levels=(PriceLevel(1, Side.SELL, Decimal("53.30"), 50, 1),),
            ),
        ]
        self.store.write_snapshots(self.snapshots)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_combines_symbol_sequence_and_time_filters(self) -> None:
        result = query_snapshots(
            self.store,
            SnapshotQuery(
                symbol="ASELS.E",
                sequence_number=200,
                start_ns=1_777_273_250_000_000_000,
            ),
        )
        self.assertEqual([item.sequence_number for item in result], [200])

    def test_latest_reverses_snapshot_order(self) -> None:
        result = query_snapshots(self.store, SnapshotQuery(order_book_id=42, latest=True))
        self.assertEqual([item.sequence_number for item in result], [200, 100])

    def test_parses_iso_and_nanosecond_times(self) -> None:
        self.assertEqual(parse_time_ns("1777273200000000123"), 1_777_273_200_000_000_123)
        self.assertEqual(
            parse_time_ns("2026-04-27T07:00:00.000123+00:00"),
            1_777_273_200_000_123_000,
        )
        with self.assertRaisesRegex(ValueError, "must include a UTC offset"):
            parse_time_ns("2026-04-27T07:00:00")

    def test_formats_table_and_writes_csv(self) -> None:
        formatted = format_snapshots([self.snapshots[0]])
        self.assertIn("ASELS.E | book=42", formatted)
        self.assertIn("53.25", formatted)

        output = Path(self.temporary_directory.name) / "result.csv"
        write_snapshot_csv(output, [self.snapshots[0]])
        with output.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(rows[0]["symbol"], "ASELS.E")
        self.assertEqual(rows[0]["price"], "53.25")


if __name__ == "__main__":
    unittest.main()
