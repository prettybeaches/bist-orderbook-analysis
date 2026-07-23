import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bist_orderbook.analysis_cache import (
    load_cached_top_of_book,
    top_of_book_cache_path,
)
from bist_orderbook.domain import BookSnapshot, PriceLevel, Side
from bist_orderbook.storage import SQLiteStore


class AnalysisCacheTest(unittest.TestCase):
    def test_persists_and_reuses_sampled_top_of_book_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "books.db"
            store = SQLiteStore(database)
            store.upsert_instruments(((42, "ASELS.E", "EQUITY", None, None),))
            store.write_snapshots(
                (
                    BookSnapshot(
                        timestamp=datetime(2026, 4, 27, 7, 0, tzinfo=UTC),
                        timestamp_ns=1_777_273_200_000_000_123,
                        sequence_number=10,
                        order_book_id=42,
                        symbol="ASELS.E",
                        levels=(
                            PriceLevel(1, Side.BUY, Decimal("53.25"), 100, 2),
                            PriceLevel(1, Side.SELL, Decimal("53.30"), 120, 3),
                        ),
                    ),
                )
            )
            modified_ns = database.stat().st_mtime_ns
            first = load_cached_top_of_book(
                database,
                database_modified_ns=modified_ns,
                order_book_id=42,
                interval_ms=1_000,
            )
            cache_path = top_of_book_cache_path(
                database,
                database_modified_ns=modified_ns,
                order_book_id=42,
                interval_ms=1_000,
            )
            self.assertTrue(cache_path.exists())

            with patch(
                "bist_orderbook.analysis_cache.load_top_of_book",
                side_effect=AssertionError("database query should not run on a cache hit"),
            ):
                second = load_cached_top_of_book(
                    database,
                    database_modified_ns=modified_ns,
                    order_book_id=42,
                    interval_ms=1_000,
                )

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
