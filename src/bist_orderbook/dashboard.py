from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bist_orderbook.analysis import PairAnalysis
from bist_orderbook.domain import BookSnapshot, Side
from bist_orderbook.storage import SQLiteStore


@dataclass(frozen=True, slots=True)
class DatabaseStatus:
    instrument_count: int
    snapshot_count: int
    price_level_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    database_size_bytes: int


def database_status(path: str | Path) -> DatabaseStatus:
    database_path = Path(path)
    store = SQLiteStore(database_path)
    with store.connect() as connection:
        instrument_count = int(connection.execute("SELECT COUNT(*) FROM instruments").fetchone()[0])
        snapshot_count = int(connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])
        price_level_count = int(
            connection.execute("SELECT COUNT(*) FROM price_levels").fetchone()[0]
        )
        first_timestamp, last_timestamp = connection.execute(
            "SELECT MIN(captured_at), MAX(captured_at) FROM snapshots"
        ).fetchone()
    return DatabaseStatus(
        instrument_count=instrument_count,
        snapshot_count=snapshot_count,
        price_level_count=price_level_count,
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        database_size_bytes=database_path.stat().st_size,
    )


def snapshot_table(snapshot: BookSnapshot) -> list[dict[str, object | None]]:
    bids = {level.level: level for level in snapshot.levels if level.side == Side.BUY}
    asks = {level.level: level for level in snapshot.levels if level.side == Side.SELL}
    return [
        {
            "Level": level_number,
            "Bid orders": bids[level_number].order_count if level_number in bids else None,
            "Bid quantity": bids[level_number].quantity if level_number in bids else None,
            "Bid price": float(bids[level_number].price) if level_number in bids else None,
            "Ask price": float(asks[level_number].price) if level_number in asks else None,
            "Ask quantity": asks[level_number].quantity if level_number in asks else None,
            "Ask orders": asks[level_number].order_count if level_number in asks else None,
        }
        for level_number in range(1, 11)
    ]


def price_chart_rows(analysis: PairAnalysis) -> list[dict[str, object]]:
    if not analysis.observations:
        return []
    spot_base = analysis.observations[0].spot.mid
    future_base = analysis.observations[0].future.mid
    rows: list[dict[str, object]] = []
    for item in analysis.observations:
        timestamp = _timestamp(item.timestamp_ns)
        rows.extend(
            (
                {
                    "time": timestamp,
                    "series": analysis.pair.spot_symbol,
                    "value": item.spot.mid / spot_base * 100,
                },
                {
                    "time": timestamp,
                    "series": analysis.pair.future_symbol,
                    "value": item.future.mid / future_base * 100,
                },
            )
        )
    return rows


def basis_chart_rows(analysis: PairAnalysis) -> list[dict[str, object]]:
    return [
        {"time": _timestamp(item.timestamp_ns), "basis_bps": item.basis_bps}
        for item in analysis.observations
    ]


def momentum_chart_rows(analysis: PairAnalysis) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in analysis.observations:
        timestamp = _timestamp(item.timestamp_ns)
        if item.spot_momentum_pct is not None:
            rows.append(
                {
                    "time": timestamp,
                    "series": analysis.pair.spot_symbol,
                    "value": item.spot_momentum_pct,
                }
            )
        if item.future_momentum_pct is not None:
            rows.append(
                {
                    "time": timestamp,
                    "series": analysis.pair.future_symbol,
                    "value": item.future_momentum_pct,
                }
            )
    return rows


def lag_chart_rows(analysis: PairAnalysis) -> list[dict[str, object]]:
    return [
        {
            "lag_seconds": item.lag_seconds,
            "correlation": item.correlation,
            "observations": item.observations,
        }
        for item in analysis.lag_correlations
        if item.correlation is not None
    ]


def analysis_csv(analysis: PairAnalysis, report: str) -> bytes:
    output = io.StringIO(newline="")
    if report == "price":
        fields = ["timestamp_ns", "spot_mid", "spot_spread", "future_mid", "future_spread"]
        rows = [
            {
                "timestamp_ns": item.timestamp_ns,
                "spot_mid": item.spot.mid,
                "spot_spread": item.spot.spread,
                "future_mid": item.future.mid,
                "future_spread": item.future.spread,
            }
            for item in analysis.observations
        ]
    elif report == "basis":
        fields = ["timestamp_ns", "spot_mid", "future_mid", "basis", "basis_bps"]
        rows = [
            {
                "timestamp_ns": item.timestamp_ns,
                "spot_mid": item.spot.mid,
                "future_mid": item.future.mid,
                "basis": item.basis,
                "basis_bps": item.basis_bps,
            }
            for item in analysis.observations
        ]
    elif report == "momentum":
        fields = [
            "timestamp_ns",
            "spot_return_pct",
            "future_return_pct",
            "spot_momentum_pct",
            "future_momentum_pct",
        ]
        rows = [
            {
                "timestamp_ns": item.timestamp_ns,
                "spot_return_pct": item.spot_return_pct,
                "future_return_pct": item.future_return_pct,
                "spot_momentum_pct": item.spot_momentum_pct,
                "future_momentum_pct": item.future_momentum_pct,
            }
            for item in analysis.observations
        ]
    elif report == "lead_lag":
        fields = ["lag_steps", "lag_seconds", "correlation", "observations"]
        rows = [
            {
                "lag_steps": item.lag_steps,
                "lag_seconds": item.lag_seconds,
                "correlation": item.correlation,
                "observations": item.observations,
            }
            for item in analysis.lag_correlations
        ]
    else:
        raise ValueError(f"unknown analysis report: {report}")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _timestamp(timestamp_ns: int) -> str:
    seconds, nanoseconds = divmod(timestamp_ns, 1_000_000_000)
    base = datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{nanoseconds:09d}Z"
