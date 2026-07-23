from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from bist_orderbook.domain import BookSnapshot


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS instruments (
    order_book_id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    market TEXT NOT NULL CHECK (market IN ('EQUITY', 'FUTURE')),
    underlying_symbol TEXT,
    expiry_date TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id INTEGER PRIMARY KEY,
    captured_at TEXT NOT NULL,
    captured_at_ns INTEGER NOT NULL,
    sequence_number INTEGER NOT NULL,
    order_book_id INTEGER NOT NULL REFERENCES instruments(order_book_id),
    UNIQUE (order_book_id, sequence_number)
);

CREATE TABLE IF NOT EXISTS price_levels (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    side TEXT NOT NULL CHECK (side IN ('B', 'S')),
    level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 10),
    price TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    order_count INTEGER NOT NULL CHECK (order_count >= 0),
    PRIMARY KEY (snapshot_id, side, level)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_time
    ON snapshots(captured_at_ns);
CREATE INDEX IF NOT EXISTS idx_snapshots_sequence
    ON snapshots(sequence_number);
CREATE INDEX IF NOT EXISTS idx_snapshots_book_time
    ON snapshots(order_book_id, captured_at_ns);
CREATE INDEX IF NOT EXISTS idx_instruments_symbol
    ON instruments(symbol);
"""


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def upsert_instruments(
        self,
        instruments: Iterable[tuple[int, str, str, str | None, str | None]],
    ) -> None:
        """Insert (book ID, symbol, market, underlying, expiry) records."""

        self.initialize()
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO instruments (
                    order_book_id, symbol, market, underlying_symbol, expiry_date
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(order_book_id) DO UPDATE SET
                    symbol = excluded.symbol,
                    market = excluded.market,
                    underlying_symbol = excluded.underlying_symbol,
                    expiry_date = excluded.expiry_date
                """,
                instruments,
            )

    def write_snapshots(self, snapshots: Iterable[BookSnapshot]) -> int:
        snapshot_list = list(snapshots)
        if not snapshot_list:
            return 0
        level_rows: list[tuple[int, str, int, str, int, int]] = []
        with self.connect() as connection:
            for snapshot in snapshot_list:
                timestamp_ns = snapshot.timestamp_ns
                if timestamp_ns is None:
                    timestamp_ns = int(snapshot.timestamp.timestamp() * 1_000_000_000)
                cursor = connection.execute(
                    """
                    INSERT INTO snapshots (
                        captured_at, captured_at_ns, sequence_number, order_book_id
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        snapshot.timestamp.isoformat(),
                        timestamp_ns,
                        snapshot.sequence_number,
                        snapshot.order_book_id,
                    ),
                )
                snapshot_id = cursor.lastrowid
                assert snapshot_id is not None
                level_rows.extend(
                    (
                        snapshot_id,
                        level.side.value,
                        level.level,
                        str(level.price),
                        level.quantity,
                        level.order_count,
                    )
                    for level in snapshot.levels
                )
            connection.executemany(
                """
                INSERT INTO price_levels (
                    snapshot_id, side, level, price, quantity, order_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                level_rows,
            )
        return len(snapshot_list)
