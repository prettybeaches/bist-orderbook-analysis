from __future__ import annotations

import csv
import io
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bist_orderbook.analysis import AlignedObservation, PairAnalysis
from bist_orderbook.domain import BookSnapshot, Side
from bist_orderbook.storage import SQLiteStore


@dataclass(frozen=True, slots=True)
class DatabaseStatus:
    instrument_count: int
    snapshot_count: int
    price_level_count: int | None
    first_timestamp: str | None
    last_timestamp: str | None
    database_size_bytes: int


def database_status(
    path: str | Path, *, include_price_level_count: bool = False
) -> DatabaseStatus:
    database_path = Path(path)
    store = SQLiteStore(database_path)
    with store.connect() as connection:
        instrument_count = int(connection.execute("SELECT COUNT(*) FROM instruments").fetchone()[0])
        snapshot_count = int(
            connection.execute("SELECT COALESCE(MAX(snapshot_id), 0) FROM snapshots").fetchone()[0]
        )
        price_level_count = (
            int(connection.execute("SELECT COUNT(*) FROM price_levels").fetchone()[0])
            if include_price_level_count
            else None
        )
        first_row = connection.execute(
            "SELECT captured_at FROM snapshots ORDER BY captured_at_ns LIMIT 1"
        ).fetchone()
        last_row = connection.execute(
            "SELECT captured_at FROM snapshots ORDER BY captured_at_ns DESC LIMIT 1"
        ).fetchone()
        first_timestamp = first_row[0] if first_row is not None else None
        last_timestamp = last_row[0] if last_row is not None else None
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


def timeline_index_at_or_before(timestamps_ns: list[int], target_ns: int) -> int:
    """Return the nearest timeline index at or before a timestamp, clamped to the range."""

    if not timestamps_ns:
        raise ValueError("timeline has no timestamps")
    return max(0, min(len(timestamps_ns) - 1, bisect_right(timestamps_ns, target_ns) - 1))


def downsample_observations(
    observations: tuple[AlignedObservation, ...],
    max_points: int | None,
) -> tuple[AlignedObservation, ...]:
    """Uniformly reduce display points while preserving the first and last observations."""

    if max_points is None or len(observations) <= max_points:
        return observations
    if max_points < 2:
        raise ValueError("maximum chart points must be at least two")
    last_index = len(observations) - 1
    indices = [round(index * last_index / (max_points - 1)) for index in range(max_points)]
    return tuple(observations[index] for index in indices)


def nearest_hover_parameter(name: str, field: str) -> dict[str, object]:
    """Build a stable nearest-value hover selection for layered Vega-Lite charts."""

    return {
        "name": name,
        "select": {
            "type": "point",
            "fields": [field],
            "nearest": True,
            "on": "pointermove",
            "clear": "pointerleave",
        },
    }


def price_chart_rows(
    analysis: PairAnalysis, mode: str = "normalized"
) -> list[dict[str, object]]:
    if not analysis.observations:
        return []
    spot_base = analysis.observations[0].spot.mid
    future_base = analysis.observations[0].future.mid
    rows: list[dict[str, object]] = []
    for item in analysis.observations:
        timestamp = _timestamp(item.timestamp_ns)
        if mode == "normalized":
            spot_value = item.spot.mid / spot_base * 100
            future_value = item.future.mid / future_base * 100
        elif mode == "mid_price":
            spot_value = item.spot.mid
            future_value = item.future.mid
        elif mode == "return":
            spot_value = item.spot_return_pct
            future_value = item.future_return_pct
        else:
            raise ValueError(f"unknown price chart mode: {mode}")
        if spot_value is None or future_value is None:
            continue
        rows.extend(
            (
                {
                    "time": timestamp,
                    "series": analysis.pair.spot_symbol,
                    "value": spot_value,
                },
                {
                    "time": timestamp,
                    "series": analysis.pair.future_symbol,
                    "value": future_value,
                },
            )
        )
    return rows


def basis_chart_rows(
    analysis: PairAnalysis, unit: str = "bps"
) -> list[dict[str, object]]:
    if unit not in {"bps", "price"}:
        raise ValueError(f"unknown basis chart unit: {unit}")
    return [
        {
            "time": _timestamp(item.timestamp_ns),
            "value": item.basis_bps if unit == "bps" else item.basis,
        }
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
