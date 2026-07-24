import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from bist_orderbook.scope import build_front_month_scope, write_symbol_pairs


class ScopeTest(unittest.TestCase):
    def test_builds_front_month_pairs_for_valid_constituents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            constituents = directory / "constituents.csv"
            constituents.write_text(
                "symbol,valid_from,valid_to\n"
                "ASELS.E,2026-04-01,2026-06-30\n"
                "BIMAS.E,2026-04-01,2026-06-30\n"
                "OLD.E,2026-01-01,2026-03-31\n",
                encoding="utf-8",
            )
            catalog = directory / "instruments.csv"
            catalog.write_text(
                "order_book_id,symbol,market,underlying_symbol,expiration_date\n"
                "42,ASELS.E,CASH,,\n"
                "43,BIMAS.E,CASH,,\n"
                "84,F_ASELS0426,FUTURE,ASELS.E,20260430\n"
                "85,F_ASELS0526,FUTURE,ASELS.E,20260525\n",
                encoding="utf-8",
            )

            result = build_front_month_scope(
                constituents,
                catalog,
                as_of=date(2026, 4, 27),
            )
            output = directory / "pairs.csv"
            write_symbol_pairs(output, result.pairs)
            with output.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))

        self.assertEqual(result.constituent_count, 2)
        self.assertEqual([pair.future_symbol for pair in result.pairs], ["F_ASELS0426"])
        self.assertEqual(result.unavailable_symbols, ("BIMAS.E",))
        self.assertEqual(rows[0]["spot_symbol"], "ASELS.E")
        self.assertEqual(rows[0]["expiration_date"], "20260430")

    def test_rejects_date_without_constituents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            constituents = directory / "constituents.csv"
            constituents.write_text(
                "symbol,valid_from,valid_to\nASELS.E,2026-04-01,2026-06-30\n",
                encoding="utf-8",
            )
            catalog = directory / "instruments.csv"
            catalog.write_text(
                "order_book_id,symbol,market,underlying_symbol,expiration_date\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "no constituents"):
                build_front_month_scope(
                    constituents,
                    catalog,
                    as_of=date(2026, 7, 1),
                )


if __name__ == "__main__":
    unittest.main()
