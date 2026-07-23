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
    load_top_of_book,
    write_analysis_summary,
    write_pair_reports,
)
from bist_orderbook.domain import BookSnapshot, PriceLevel, Side
from bist_orderbook.storage import SQLiteStore


def book(second: int, mid: str) -> TopOfBook:
    midpoint = Decimal(mid)
    return TopOfBook(
        timestamp_ns=second * 1_000_000_000,
        bid=midpoint - Decimal("0.05"),
        ask=midpoint + Decimal("0.05"),
        bid_quantity=100,
        ask_quantity=120,
    )


class PairAnalysisTest(unittest.TestCase):
    def setUp(self) -> None:
        spot_values = ("100", "101", "102", "101", "103", "104")
        spot = [book(index, value) for index, value in enumerate(spot_values)]
        future = [
            book(index, value)
            for index, value in enumerate(("105", "106", "107", "106", "108", "109"))
        ]
        self.observations = align_top_of_books(
            spot,
            future,
            interval_ns=1_000_000_000,
            max_staleness_ns=1_000_000_000,
            momentum_periods=2,
        )
        self.pair = SymbolPair("ASELS.E", 42, "F_ASELS0426", 84, "20260430")
        self.lags = calculate_lag_correlations(
            self.observations,
            max_lag_steps=2,
            interval_seconds=1,
        )
        self.analysis = PairAnalysis(self.pair, self.observations, self.lags)

    def test_aligns_prices_and_calculates_metrics(self) -> None:
        self.assertEqual(len(self.observations), 6)
        self.assertEqual(self.observations[0].basis, 5.0)
        self.assertIsNotNone(self.observations[1].spot_return_pct)
        self.assertIsNotNone(self.observations[2].spot_momentum_pct)
        lag_zero = next(item for item in self.lags if item.lag_steps == 0)
        self.assertGreater(lag_zero.correlation or 0, 0.99)

    def test_writes_four_csv_and_four_svg_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = write_pair_reports(temporary_directory, self.analysis)
            self.assertEqual(len(paths), 8)
            for path in paths.values():
                self.assertTrue(path.exists())
                self.assertGreater(path.stat().st_size, 0)
            self.assertIn("<svg", paths["price_chart"].read_text(encoding="utf-8"))

            summary = Path(temporary_directory) / "summary.csv"
            write_analysis_summary(summary, [self.analysis])
            with summary.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(rows[0]["spot_symbol"], "ASELS.E")
            self.assertEqual(rows[0]["observations"], "6")

    def test_sampled_top_of_book_matches_alignment_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = SQLiteStore(Path(temporary_directory) / "books.db")
            store.upsert_instruments(((42, "ASELS.E", "EQUITY", None, None),))
            timestamps_ns = (50_000_000, 90_000_000, 100_000_000, 150_000_000, 190_000_000, 250_000_000)
            snapshots = tuple(
                BookSnapshot(
                    timestamp=datetime.fromtimestamp(timestamp_ns / 1_000_000_000, UTC),
                    timestamp_ns=timestamp_ns,
                    sequence_number=index,
                    order_book_id=42,
                    symbol="ASELS.E",
                    levels=(
                        PriceLevel(1, Side.BUY, Decimal(index), 100, 1),
                        PriceLevel(1, Side.SELL, Decimal(index + 1), 100, 1),
                    ),
                )
                for index, timestamp_ns in enumerate(timestamps_ns, start=1)
            )
            store.write_snapshots(snapshots)

            full = load_top_of_book(store, 42)
            sampled = load_top_of_book(store, 42, sample_interval_ns=100_000_000)

        self.assertEqual([item.timestamp_ns for item in sampled], [100_000_000, 190_000_000, 250_000_000])
        full_aligned = align_top_of_books(
            full,
            full,
            interval_ns=100_000_000,
            max_staleness_ns=100_000_000,
            momentum_periods=1,
        )
        sampled_aligned = align_top_of_books(
            sampled,
            sampled,
            interval_ns=100_000_000,
            max_staleness_ns=100_000_000,
            momentum_periods=1,
        )
        self.assertEqual(full_aligned, sampled_aligned)


if __name__ == "__main__":
    unittest.main()
