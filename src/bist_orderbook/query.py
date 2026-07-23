from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from bist_orderbook.domain import BookSnapshot, PriceLevel, Side
from bist_orderbook.storage import SQLiteStore


@dataclass(frozen=True, slots=True)
class SnapshotQuery:
    symbol: str | None = None
    order_book_id: int | None = None
    sequence_number: int | None = None
    start_ns: int | None = None
    end_ns: int | None = None
    limit: int = 20
    latest: bool = False
    populated_only: bool = False

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("query limit must be positive")
        if self.order_book_id is not None and self.order_book_id < 0:
            raise ValueError("order book ID cannot be negative")
        if self.sequence_number is not None and self.sequence_number < 0:
            raise ValueError("sequence number cannot be negative")
        if self.start_ns is not None and self.end_ns is not None:
            if self.start_ns > self.end_ns:
                raise ValueError("start time cannot be later than end time")


def parse_time_ns(value: str) -> int:
    """Parse an integer nanosecond timestamp or an offset-aware ISO-8601 value."""

    try:
        return int(value)
    except ValueError:
        pass

    match = re.fullmatch(
        r"(?P<base>.+?)(?:\.(?P<fraction>\d{1,9}))?(?P<offset>Z|[+-]\d{2}:\d{2})",
        value,
    )
    if match is not None:
        offset = "+00:00" if match["offset"] == "Z" else match["offset"]
        try:
            timestamp = datetime.fromisoformat(f"{match['base']}{offset}")
        except ValueError as error:
            raise ValueError(f"invalid time value: {value}") from error
        fraction_ns = int((match["fraction"] or "").ljust(9, "0"))
        return int(timestamp.timestamp()) * 1_000_000_000 + fraction_ns

    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        timestamp = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"invalid time value: {value}") from error
    if timestamp.tzinfo is None:
        raise ValueError("ISO-8601 time values must include a UTC offset")
    raise ValueError(f"invalid time value: {value}")


def query_snapshots(store: SQLiteStore, query: SnapshotQuery) -> list[BookSnapshot]:
    conditions: list[str] = []
    parameters: list[object] = []
    if query.symbol is not None:
        conditions.append("i.symbol = ?")
        parameters.append(query.symbol)
    if query.order_book_id is not None:
        conditions.append("s.order_book_id = ?")
        parameters.append(query.order_book_id)
    if query.sequence_number is not None:
        conditions.append("s.sequence_number = ?")
        parameters.append(query.sequence_number)
    if query.start_ns is not None:
        conditions.append("s.captured_at_ns >= ?")
        parameters.append(query.start_ns)
    if query.end_ns is not None:
        conditions.append("s.captured_at_ns <= ?")
        parameters.append(query.end_ns)
    if query.populated_only:
        conditions.append(
            "EXISTS (SELECT 1 FROM price_levels AS available "
            "WHERE available.snapshot_id = s.snapshot_id)"
        )

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    direction = "DESC" if query.latest else "ASC"
    parameters.append(query.limit)
    sql = f"""
        WITH selected AS (
            SELECT
                s.snapshot_id,
                s.captured_at,
                s.captured_at_ns,
                s.sequence_number,
                s.order_book_id,
                i.symbol
            FROM snapshots AS s
            JOIN instruments AS i USING (order_book_id)
            {where}
            ORDER BY s.captured_at_ns {direction}, s.sequence_number {direction}
            LIMIT ?
        )
        SELECT
            selected.snapshot_id,
            selected.captured_at,
            selected.captured_at_ns,
            selected.sequence_number,
            selected.order_book_id,
            selected.symbol,
            p.side,
            p.level,
            p.price,
            p.quantity,
            p.order_count
        FROM selected
        LEFT JOIN price_levels AS p USING (snapshot_id)
        ORDER BY selected.captured_at_ns {direction}, selected.sequence_number {direction},
                 p.side, p.level
    """
    with store.connect() as connection:
        rows = connection.execute(sql, parameters).fetchall()

    snapshots: list[BookSnapshot] = []
    current_id: int | None = None
    levels: list[PriceLevel] = []
    metadata: tuple[object, ...] | None = None
    for row in rows:
        snapshot_id = int(row[0])
        if current_id is not None and snapshot_id != current_id:
            assert metadata is not None
            snapshots.append(_build_snapshot(metadata, levels))
            levels = []
        current_id = snapshot_id
        metadata = row[1:6]
        if row[6] is not None:
            levels.append(
                PriceLevel(
                    side=Side(row[6]),
                    level=int(row[7]),
                    price=Decimal(row[8]),
                    quantity=int(row[9]),
                    order_count=int(row[10]),
                )
            )
    if current_id is not None and metadata is not None:
        snapshots.append(_build_snapshot(metadata, levels))
    return snapshots


def _build_snapshot(metadata: tuple[object, ...], levels: list[PriceLevel]) -> BookSnapshot:
    captured_at, captured_at_ns, sequence_number, order_book_id, symbol = metadata
    return BookSnapshot(
        timestamp=datetime.fromisoformat(str(captured_at)),
        timestamp_ns=int(captured_at_ns),
        sequence_number=int(sequence_number),
        order_book_id=int(order_book_id),
        symbol=str(symbol),
        levels=tuple(levels),
    )


def format_snapshots(snapshots: list[BookSnapshot]) -> str:
    if not snapshots:
        return "No snapshots found."
    output: list[str] = []
    for snapshot in snapshots:
        output.append(
            f"{snapshot.symbol} | book={snapshot.order_book_id} | "
            f"sequence={snapshot.sequence_number} | time={snapshot.timestamp.isoformat()} | "
            f"time_ns={snapshot.timestamp_ns}"
        )
        output.append(
            "Lvl | Bid orders | Bid quantity | Bid price | Ask price | Ask quantity | Ask orders"
        )
        output.append(
            "----+------------+--------------+-----------+-----------+--------------+-----------"
        )
        bids = {level.level: level for level in snapshot.levels if level.side == Side.BUY}
        asks = {level.level: level for level in snapshot.levels if level.side == Side.SELL}
        for level_number in range(1, 11):
            bid = bids.get(level_number)
            ask = asks.get(level_number)
            output.append(
                f"{level_number:>3} | "
                f"{_cell(bid.order_count if bid else None, 10)} | "
                f"{_cell(bid.quantity if bid else None, 12)} | "
                f"{_cell(bid.price if bid else None, 9)} | "
                f"{_cell(ask.price if ask else None, 9)} | "
                f"{_cell(ask.quantity if ask else None, 12)} | "
                f"{_cell(ask.order_count if ask else None, 10)}"
            )
        output.append("")
    return "\n".join(output).rstrip()


def _cell(value: object | None, width: int) -> str:
    return f"{'' if value is None else value:>{width}}"


def write_snapshot_csv(path: str | Path, snapshots: list[BookSnapshot]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "timestamp",
        "timestamp_ns",
        "sequence_number",
        "order_book_id",
        "symbol",
        "side",
        "level",
        "price",
        "quantity",
        "order_count",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for snapshot in snapshots:
            for level in snapshot.levels:
                writer.writerow(
                    {
                        "timestamp": snapshot.timestamp.isoformat(),
                        "timestamp_ns": snapshot.timestamp_ns,
                        "sequence_number": snapshot.sequence_number,
                        "order_book_id": snapshot.order_book_id,
                        "symbol": snapshot.symbol,
                        "side": level.side.value,
                        "level": level.level,
                        "price": level.price,
                        "quantity": level.quantity,
                        "order_count": level.order_count,
                    }
                )
