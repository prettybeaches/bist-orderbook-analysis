import sqlite3
import tempfile
import unittest
from pathlib import Path

from bist_orderbook.storage import SQLiteStore


class SQLiteStoreTest(unittest.TestCase):
    def test_initialize_creates_queryable_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orderbook.db"
            SQLiteStore(path).initialize()

            with sqlite3.connect(path) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }

            self.assertLessEqual({"instruments", "snapshots", "price_levels"}, tables)


if __name__ == "__main__":
    unittest.main()

