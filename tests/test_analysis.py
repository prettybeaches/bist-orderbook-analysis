import csv
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from bist_orderbook.analysis import (
    PairAnalysis,
    SymbolPair,
    TopOfBook,
    align_top_of_books,
    calculate_lag_correlations,
    write_analysis_summary,
    write_pair_reports,
)


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


if __name__ == "__main__":
    unittest.main()
