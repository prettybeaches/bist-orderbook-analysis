import csv
import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bist_orderbook.analysis import (
    PairAnalysis,
    SymbolPair,
    TopOfBook,
    align_top_of_books,
    calculate_lag_correlations,
)
from bist_orderbook.dashboard import (
    analysis_csv,
    basis_chart_rows,
    database_status,
    downsample_observations,
    lag_chart_rows,
    price_chart_rows,
    snapshot_table,
    timeline_index_at_or_before,
)
from bist_orderbook.domain import BookSnapshot, PriceLevel, Side
from bist_orderbook.storage import SQLiteStore


class DashboardHelperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = BookSnapshot(
            timestamp=datetime(2026, 4, 27, 7, 0, tzinfo=UTC),
            timestamp_ns=1_777_273_200_000_000_123,
            sequence_number=10,
            order_book_id=42,
            symbol="ASELS.E",
            levels=(
                PriceLevel(1, Side.BUY, Decimal("53.25"), 100, 2),
                PriceLevel(1, Side.SELL, Decimal("53.30"), 120, 3),
            ),
        )

    def test_database_status_and_book_table(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "orderbook.db"
            store = SQLiteStore(path)
            store.upsert_instruments(((42, "ASELS.E", "EQUITY", None, None),))
            store.write_snapshots((self.snapshot,))
            status = database_status(path, include_price_level_count=True)

        self.assertEqual(status.instrument_count, 1)
        self.assertEqual(status.snapshot_count, 1)
        self.assertEqual(status.price_level_count, 2)
        rows = snapshot_table(self.snapshot)
        self.assertEqual(rows[0]["Bid price"], 53.25)
        self.assertEqual(rows[0]["Ask quantity"], 120)
        self.assertEqual(len(rows), 10)

    def test_chart_rows_and_download_csv(self) -> None:
        spot = [
            TopOfBook(index * 1_000_000_000, Decimal("10"), Decimal("11"), 10, 10)
            for index in range(4)
        ]
        future = [
            TopOfBook(index * 1_000_000_000, Decimal("11"), Decimal("12"), 10, 10)
            for index in range(4)
        ]
        observations = align_top_of_books(
            spot,
            future,
            interval_ns=1_000_000_000,
            max_staleness_ns=1_000_000_000,
            momentum_periods=2,
        )
        lags = calculate_lag_correlations(
            observations,
            max_lag_steps=1,
            interval_seconds=1,
        )
        analysis = PairAnalysis(
            SymbolPair("ASELS.E", 42, "F_ASELS0426", 84, "20260430"),
            observations,
            lags,
        )

        self.assertEqual(len(price_chart_rows(analysis)), 8)
        self.assertEqual(price_chart_rows(analysis, "mid_price")[0]["value"], 10.5)
        self.assertEqual(len(price_chart_rows(analysis, "return")), 6)
        self.assertEqual(basis_chart_rows(analysis, "price")[0]["value"], 1.0)
        self.assertGreater(basis_chart_rows(analysis, "bps")[0]["value"], 900)
        self.assertIsInstance(lag_chart_rows(analysis), list)
        rows = list(csv.DictReader(analysis_csv(analysis, "basis").decode().splitlines()))
        self.assertEqual(len(rows), 4)
        self.assertEqual(float(rows[0]["basis"]), 1.0)

    def test_finds_timeline_index_at_or_before_and_clamps_boundaries(self) -> None:
        timestamps = [100, 200, 300]
        self.assertEqual(timeline_index_at_or_before(timestamps, 50), 0)
        self.assertEqual(timeline_index_at_or_before(timestamps, 200), 1)
        self.assertEqual(timeline_index_at_or_before(timestamps, 250), 1)
        self.assertEqual(timeline_index_at_or_before(timestamps, 999), 2)

    def test_downsamples_chart_observations_and_preserves_boundaries(self) -> None:
        books = [
            TopOfBook(index * 1_000_000_000, Decimal("10"), Decimal("11"), 10, 10)
            for index in range(10)
        ]
        observations = align_top_of_books(
            books,
            books,
            interval_ns=1_000_000_000,
            max_staleness_ns=1_000_000_000,
            momentum_periods=2,
        )
        sampled = downsample_observations(observations, 3)
        self.assertEqual(len(sampled), 3)
        self.assertEqual(sampled[0], observations[0])
        self.assertEqual(sampled[-1], observations[-1])


if __name__ == "__main__":
    unittest.main()
