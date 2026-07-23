from __future__ import annotations

import csv
import gzip
import os
import re
import tempfile
from decimal import Decimal
from pathlib import Path

from bist_orderbook.analysis import TopOfBook, load_top_of_book
from bist_orderbook.storage import SQLiteStore


def load_cached_top_of_book(
    database: str | Path,
    *,
    database_modified_ns: int,
    order_book_id: int,
    interval_ms: int,
) -> tuple[TopOfBook, ...]:
    """Load sampled top-of-book data from a persistent cache, creating it on a miss."""

    database_path = Path(database)
    cache_path = top_of_book_cache_path(
        database_path,
        database_modified_ns=database_modified_ns,
        order_book_id=order_book_id,
        interval_ms=interval_ms,
    )
    if cache_path.exists():
        try:
            return _read_cache(cache_path)
        except (OSError, ValueError, csv.Error):
            cache_path.unlink(missing_ok=True)

    books = tuple(
        load_top_of_book(
            SQLiteStore(database_path),
            order_book_id,
            sample_interval_ns=interval_ms * 1_000_000,
        )
    )
    _write_cache(cache_path, books)
    return books


def top_of_book_cache_path(
    database: str | Path,
    *,
    database_modified_ns: int,
    order_book_id: int,
    interval_ms: int,
) -> Path:
    database_path = Path(database)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", database_path.name)
    fingerprint = f"{database_path.stat().st_size}-{database_modified_ns}"
    filename = f"{safe_name}-{fingerprint}-{order_book_id}-{interval_ms}.csv.gz"
    return database_path.parent / ".cache" / filename


def _read_cache(path: Path) -> tuple[TopOfBook, ...]:
    with gzip.open(path, mode="rt", encoding="utf-8", newline="") as source:
        return tuple(
            TopOfBook(
                timestamp_ns=int(row[0]),
                bid=Decimal(row[1]),
                ask=Decimal(row[2]),
                bid_quantity=int(row[3]),
                ask_quantity=int(row[4]),
            )
            for row in csv.reader(source)
        )


def _write_cache(path: Path, books: tuple[TopOfBook, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    temporary_path = Path(temporary_file.name)
    temporary_file.close()
    try:
        with gzip.open(temporary_path, mode="wt", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerows(
                (
                    item.timestamp_ns,
                    item.bid,
                    item.ask,
                    item.bid_quantity,
                    item.ask_quantity,
                )
                for item in books
            )
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
