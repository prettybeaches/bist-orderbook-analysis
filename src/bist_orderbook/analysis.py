from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from pathlib import Path
from statistics import fmean, pstdev

from bist_orderbook.storage import SQLiteStore


@dataclass(frozen=True, slots=True)
class SymbolPair:
    spot_symbol: str
    spot_order_book_id: int
    future_symbol: str
    future_order_book_id: int
    expiration_date: str

    @property
    def name(self) -> str:
        return f"{self.spot_symbol}__{self.future_symbol}"


@dataclass(frozen=True, slots=True)
class TopOfBook:
    timestamp_ns: int
    bid: Decimal
    ask: Decimal
    bid_quantity: int
    ask_quantity: int

    @property
    def mid(self) -> float:
        return float((self.bid + self.ask) / 2)

    @property
    def spread(self) -> float:
        return float(self.ask - self.bid)


@dataclass(frozen=True, slots=True)
class AlignedObservation:
    timestamp_ns: int
    spot: TopOfBook
    future: TopOfBook
    spot_return_pct: float | None
    future_return_pct: float | None
    spot_momentum_pct: float | None
    future_momentum_pct: float | None

    @property
    def basis(self) -> float:
        return self.future.mid - self.spot.mid

    @property
    def basis_bps(self) -> float:
        return self.basis / self.spot.mid * 10_000


@dataclass(frozen=True, slots=True)
class LagCorrelation:
    lag_steps: int
    lag_seconds: float
    correlation: float | None
    observations: int


@dataclass(frozen=True, slots=True)
class PairAnalysis:
    pair: SymbolPair
    observations: tuple[AlignedObservation, ...]
    lag_correlations: tuple[LagCorrelation, ...]


def load_symbol_pairs(path: str | Path) -> tuple[SymbolPair, ...]:
    with Path(path).open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    return tuple(
        SymbolPair(
            spot_symbol=row["spot_symbol"],
            spot_order_book_id=int(row["spot_order_book_id"]),
            future_symbol=row["future_symbol"],
            future_order_book_id=int(row["future_order_book_id"]),
            expiration_date=row["expiration_date"],
        )
        for row in rows
    )


def load_top_of_book(
    store: SQLiteStore,
    order_book_id: int,
    *,
    sample_interval_ns: int | None = None,
) -> list[TopOfBook]:
    if sample_interval_ns is not None and sample_interval_ns <= 0:
        raise ValueError("sample interval must be positive")
    if sample_interval_ns is None:
        sql = """
        SELECT
            s.captured_at_ns,
            bid.price,
            ask.price,
            bid.quantity,
            ask.quantity
        FROM snapshots AS s
        JOIN price_levels AS bid
          ON bid.snapshot_id = s.snapshot_id AND bid.side = 'B' AND bid.level = 1
        JOIN price_levels AS ask
          ON ask.snapshot_id = s.snapshot_id AND ask.side = 'S' AND ask.level = 1
        WHERE s.order_book_id = ?
        ORDER BY s.captured_at_ns
        """
        parameters: tuple[object, ...] = (order_book_id,)
    else:
        sql = """
        WITH selected AS (
            SELECT
                MAX(s.snapshot_id) AS snapshot_id,
                MAX(s.captured_at_ns) AS captured_at_ns
            FROM snapshots AS s
            WHERE s.order_book_id = ?
            GROUP BY (s.captured_at_ns + ? - 1) / ?
        )
        SELECT
            selected.captured_at_ns,
            bid.price,
            ask.price,
            bid.quantity,
            ask.quantity
        FROM selected
        LEFT JOIN price_levels AS bid
          ON bid.snapshot_id = selected.snapshot_id AND bid.side = 'B' AND bid.level = 1
        LEFT JOIN price_levels AS ask
          ON ask.snapshot_id = selected.snapshot_id AND ask.side = 'S' AND ask.level = 1
        ORDER BY selected.captured_at_ns
        """
        parameters = (order_book_id, sample_interval_ns, sample_interval_ns)
    with store.connect() as connection:
        rows = connection.execute(sql, parameters).fetchall()
        if sample_interval_ns is not None:
            fallback_sql = """
                SELECT
                    s.captured_at_ns,
                    bid.price,
                    ask.price,
                    bid.quantity,
                    ask.quantity
                FROM snapshots AS s
                JOIN price_levels AS bid
                  ON bid.snapshot_id = s.snapshot_id AND bid.side = 'B' AND bid.level = 1
                JOIN price_levels AS ask
                  ON ask.snapshot_id = s.snapshot_id AND ask.side = 'S' AND ask.level = 1
                WHERE s.order_book_id = ?
                  AND s.captured_at_ns > ?
                  AND s.captured_at_ns <= ?
                ORDER BY s.captured_at_ns DESC
                LIMIT 1
            """
            repaired_rows: list[tuple[object, ...]] = []
            for row in rows:
                if row[1] is not None and row[2] is not None:
                    repaired_rows.append(row)
                    continue
                bucket_end_ns = (
                    (int(row[0]) + sample_interval_ns - 1) // sample_interval_ns
                ) * sample_interval_ns
                fallback = connection.execute(
                    fallback_sql,
                    (
                        order_book_id,
                        bucket_end_ns - sample_interval_ns,
                        bucket_end_ns,
                    ),
                ).fetchone()
                if fallback is not None:
                    repaired_rows.append(fallback)
            rows = repaired_rows
    return [
        TopOfBook(
            timestamp_ns=int(row[0]),
            bid=Decimal(row[1]),
            ask=Decimal(row[2]),
            bid_quantity=int(row[3]),
            ask_quantity=int(row[4]),
        )
        for row in rows
    ]


def align_top_of_books(
    spot: list[TopOfBook],
    future: list[TopOfBook],
    *,
    interval_ns: int,
    max_staleness_ns: int,
    momentum_periods: int,
) -> tuple[AlignedObservation, ...]:
    if not spot or not future:
        return ()
    if interval_ns <= 0 or max_staleness_ns < 0 or momentum_periods <= 0:
        raise ValueError("alignment parameters are invalid")
    start = max(spot[0].timestamp_ns, future[0].timestamp_ns)
    end = min(spot[-1].timestamp_ns, future[-1].timestamp_ns)
    timestamp = ((start + interval_ns - 1) // interval_ns) * interval_ns
    spot_index = future_index = 0
    aligned_books: list[tuple[int, TopOfBook, TopOfBook]] = []
    current_spot: TopOfBook | None = None
    current_future: TopOfBook | None = None
    while timestamp <= end:
        while spot_index < len(spot) and spot[spot_index].timestamp_ns <= timestamp:
            current_spot = spot[spot_index]
            spot_index += 1
        while future_index < len(future) and future[future_index].timestamp_ns <= timestamp:
            current_future = future[future_index]
            future_index += 1
        if (
            current_spot is not None
            and current_future is not None
            and timestamp - current_spot.timestamp_ns <= max_staleness_ns
            and timestamp - current_future.timestamp_ns <= max_staleness_ns
        ):
            aligned_books.append((timestamp, current_spot, current_future))
        timestamp += interval_ns

    observations: list[AlignedObservation] = []
    for index, (timestamp_ns, spot_book, future_book) in enumerate(aligned_books):
        previous = aligned_books[index - 1] if index else None
        momentum_base = (
            aligned_books[index - momentum_periods] if index >= momentum_periods else None
        )
        observations.append(
            AlignedObservation(
                timestamp_ns=timestamp_ns,
                spot=spot_book,
                future=future_book,
                spot_return_pct=(
                    _percent_change(spot_book.mid, previous[1].mid) if previous else None
                ),
                future_return_pct=(
                    _percent_change(future_book.mid, previous[2].mid) if previous else None
                ),
                spot_momentum_pct=(
                    _percent_change(spot_book.mid, momentum_base[1].mid)
                    if momentum_base
                    else None
                ),
                future_momentum_pct=(
                    _percent_change(future_book.mid, momentum_base[2].mid)
                    if momentum_base
                    else None
                ),
            )
        )
    return tuple(observations)


def _percent_change(value: float, previous: float) -> float | None:
    return (value / previous - 1) * 100 if previous else None


def calculate_lag_correlations(
    observations: tuple[AlignedObservation, ...],
    *,
    max_lag_steps: int,
    interval_seconds: float,
) -> tuple[LagCorrelation, ...]:
    spot_returns = [item.spot_return_pct for item in observations]
    future_returns = [item.future_return_pct for item in observations]
    results: list[LagCorrelation] = []
    for lag in range(-max_lag_steps, max_lag_steps + 1):
        pairs: list[tuple[float, float]] = []
        for spot_index, spot_value in enumerate(spot_returns):
            future_index = spot_index - lag
            if (
                spot_value is not None
                and 0 <= future_index < len(future_returns)
                and future_returns[future_index] is not None
            ):
                pairs.append((spot_value, future_returns[future_index]))
        correlation = _correlation([item[0] for item in pairs], [item[1] for item in pairs])
        results.append(
            LagCorrelation(
                lag_steps=lag,
                lag_seconds=lag * interval_seconds,
                correlation=correlation,
                observations=len(pairs),
            )
        )
    return tuple(results)


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2:
        return None
    left_mean = fmean(left)
    right_mean = fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_variance = sum((x - left_mean) ** 2 for x in left)
    right_variance = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_variance * right_variance)
    return numerator / denominator if denominator else None


def analyze_pair(
    store: SQLiteStore,
    pair: SymbolPair,
    *,
    interval_ms: int,
    max_staleness_ms: int,
    momentum_periods: int,
    max_lag_steps: int,
) -> PairAnalysis:
    interval_ns = interval_ms * 1_000_000
    return analyze_top_of_books(
        pair,
        load_top_of_book(
            store,
            pair.spot_order_book_id,
            sample_interval_ns=interval_ns,
        ),
        load_top_of_book(
            store,
            pair.future_order_book_id,
            sample_interval_ns=interval_ns,
        ),
        interval_ms=interval_ms,
        max_staleness_ms=max_staleness_ms,
        momentum_periods=momentum_periods,
        max_lag_steps=max_lag_steps,
    )


def analyze_top_of_books(
    pair: SymbolPair,
    spot: list[TopOfBook] | tuple[TopOfBook, ...],
    future: list[TopOfBook] | tuple[TopOfBook, ...],
    *,
    interval_ms: int,
    max_staleness_ms: int,
    momentum_periods: int,
    max_lag_steps: int,
) -> PairAnalysis:
    observations = align_top_of_books(
        list(spot),
        list(future),
        interval_ns=interval_ms * 1_000_000,
        max_staleness_ns=max_staleness_ms * 1_000_000,
        momentum_periods=momentum_periods,
    )
    lag_correlations = calculate_lag_correlations(
        observations,
        max_lag_steps=max_lag_steps,
        interval_seconds=interval_ms / 1_000,
    )
    return PairAnalysis(pair, observations, lag_correlations)


def write_pair_reports(output_root: str | Path, analysis: PairAnalysis) -> dict[str, Path]:
    directory = Path(output_root) / _safe_name(analysis.pair.name)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "price_csv": directory / "price.csv",
        "price_chart": directory / "price.svg",
        "basis_csv": directory / "basis.csv",
        "basis_chart": directory / "basis.svg",
        "momentum_csv": directory / "momentum.csv",
        "momentum_chart": directory / "momentum.svg",
        "lead_lag_csv": directory / "lead_lag.csv",
        "lead_lag_chart": directory / "lead_lag.svg",
    }
    _write_price_csv(paths["price_csv"], analysis)
    _write_basis_csv(paths["basis_csv"], analysis)
    _write_momentum_csv(paths["momentum_csv"], analysis)
    _write_lead_lag_csv(paths["lead_lag_csv"], analysis)
    _write_analysis_charts(paths, analysis)
    return paths


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _timestamp_text(timestamp_ns: int) -> str:
    seconds, nanoseconds = divmod(timestamp_ns, 1_000_000_000)
    base = datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{nanoseconds:09d}+00:00"


def _write_rows(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_price_csv(path: Path, analysis: PairAnalysis) -> None:
    fields = [
        "timestamp",
        "timestamp_ns",
        "spot_symbol",
        "spot_bid",
        "spot_ask",
        "spot_mid",
        "spot_spread",
        "future_symbol",
        "future_bid",
        "future_ask",
        "future_mid",
        "future_spread",
    ]
    rows = [
        {
            "timestamp": _timestamp_text(item.timestamp_ns),
            "timestamp_ns": item.timestamp_ns,
            "spot_symbol": analysis.pair.spot_symbol,
            "spot_bid": item.spot.bid,
            "spot_ask": item.spot.ask,
            "spot_mid": item.spot.mid,
            "spot_spread": item.spot.spread,
            "future_symbol": analysis.pair.future_symbol,
            "future_bid": item.future.bid,
            "future_ask": item.future.ask,
            "future_mid": item.future.mid,
            "future_spread": item.future.spread,
        }
        for item in analysis.observations
    ]
    _write_rows(path, fields, rows)


def _write_basis_csv(path: Path, analysis: PairAnalysis) -> None:
    fields = ["timestamp", "timestamp_ns", "spot_mid", "future_mid", "basis", "basis_bps"]
    rows = [
        {
            "timestamp": _timestamp_text(item.timestamp_ns),
            "timestamp_ns": item.timestamp_ns,
            "spot_mid": item.spot.mid,
            "future_mid": item.future.mid,
            "basis": item.basis,
            "basis_bps": item.basis_bps,
        }
        for item in analysis.observations
    ]
    _write_rows(path, fields, rows)


def _write_momentum_csv(path: Path, analysis: PairAnalysis) -> None:
    fields = [
        "timestamp",
        "timestamp_ns",
        "spot_return_pct",
        "future_return_pct",
        "spot_momentum_pct",
        "future_momentum_pct",
    ]
    rows = [
        {
            "timestamp": _timestamp_text(item.timestamp_ns),
            "timestamp_ns": item.timestamp_ns,
            "spot_return_pct": item.spot_return_pct,
            "future_return_pct": item.future_return_pct,
            "spot_momentum_pct": item.spot_momentum_pct,
            "future_momentum_pct": item.future_momentum_pct,
        }
        for item in analysis.observations
    ]
    _write_rows(path, fields, rows)


def _write_lead_lag_csv(path: Path, analysis: PairAnalysis) -> None:
    fields = ["lag_steps", "lag_seconds", "correlation", "observations", "interpretation"]
    rows = [
        {
            "lag_steps": item.lag_steps,
            "lag_seconds": item.lag_seconds,
            "correlation": item.correlation,
            "observations": item.observations,
            "interpretation": (
                "future leads spot" if item.lag_steps > 0 else
                "spot leads future" if item.lag_steps < 0 else
                "contemporaneous"
            ),
        }
        for item in analysis.lag_correlations
    ]
    _write_rows(path, fields, rows)


def _write_analysis_charts(paths: dict[str, Path], analysis: PairAnalysis) -> None:
    observations = analysis.observations
    times = [item.timestamp_ns / 1_000_000_000 for item in observations]
    if observations:
        spot_base = observations[0].spot.mid
        future_base = observations[0].future.mid
    else:
        spot_base = future_base = 1.0
    _write_line_svg(
        paths["price_chart"],
        f"Normalized mid-price: {analysis.pair.spot_symbol} vs {analysis.pair.future_symbol}",
        times,
        [
            (analysis.pair.spot_symbol, [item.spot.mid / spot_base * 100 for item in observations]),
            (
                analysis.pair.future_symbol,
                [item.future.mid / future_base * 100 for item in observations],
            ),
        ],
        "Index (first observation = 100)",
    )
    _write_line_svg(
        paths["basis_chart"],
        f"Futures basis: {analysis.pair.name}",
        times,
        [("Basis", [item.basis_bps for item in observations])],
        "Basis (bps)",
    )
    _write_line_svg(
        paths["momentum_chart"],
        f"Momentum: {analysis.pair.spot_symbol} vs {analysis.pair.future_symbol}",
        times,
        [
            (analysis.pair.spot_symbol, [item.spot_momentum_pct for item in observations]),
            (analysis.pair.future_symbol, [item.future_momentum_pct for item in observations]),
        ],
        "Momentum (%)",
    )
    lag_x = [item.lag_seconds for item in analysis.lag_correlations]
    _write_line_svg(
        paths["lead_lag_chart"],
        f"Lead-lag return correlation: {analysis.pair.name}",
        lag_x,
        [("Correlation", [item.correlation for item in analysis.lag_correlations])],
        "Correlation",
        x_is_time=False,
        x_label="Lag (seconds; positive means futures lead)",
    )


def _write_line_svg(
    path: Path,
    title: str,
    x_values: list[float],
    series: list[tuple[str, list[float | None]]],
    y_label: str,
    *,
    x_is_time: bool = True,
    x_label: str = "Time (UTC)",
) -> None:
    width, height = 1100, 520
    left, right, top, bottom = 90, 30, 55, 70
    plot_width, plot_height = width - left - right, height - top - bottom
    points = [value for _, values in series for value in values if value is not None]
    if not x_values or not points:
        path.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
            f'<text x="40" y="60">{escape(title)}: no aligned observations</text></svg>',
            encoding="utf-8",
        )
        return
    x_min, x_max = min(x_values), max(x_values)
    y_min, y_max = min(points), max(points)
    if x_min == x_max:
        x_max = x_min + 1
    if y_min == y_max:
        padding = abs(y_min) * 0.01 or 1.0
        y_min -= padding
        y_max += padding
    else:
        padding = (y_max - y_min) * 0.08
        y_min -= padding
        y_max += padding

    def x_position(value: float) -> float:
        return left + (value - x_min) / (x_max - x_min) * plot_width

    def y_position(value: float) -> float:
        return top + (y_max - value) / (y_max - y_min) * plot_height

    colors = ("#1f77b4", "#d62728", "#2ca02c", "#9467bd")
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="30" font-family="sans-serif" font-size="18" '
        f'font-weight="600" fill="#111827">{escape(title)}</text>',
    ]
    for index in range(6):
        fraction = index / 5
        y_value = y_max - fraction * (y_max - y_min)
        y = top + fraction * plot_height
        svg.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_width}" y2="{y:.2f}" '
            'stroke="#e5e7eb" stroke-width="1"/>'
        )
        svg.append(
            f'<text x="{left - 10}" y="{y + 4:.2f}" text-anchor="end" '
            f'font-family="sans-serif" font-size="11" fill="#4b5563">{y_value:.3f}</text>'
        )
    for index in range(6):
        fraction = index / 5
        x_value = x_min + fraction * (x_max - x_min)
        x = left + fraction * plot_width
        label = (
            datetime.fromtimestamp(x_value, UTC).strftime("%H:%M:%S")
            if x_is_time
            else f"{x_value:g}"
        )
        svg.append(
            f'<text x="{x:.2f}" y="{top + plot_height + 22}" text-anchor="middle" '
            f'font-family="sans-serif" font-size="11" fill="#4b5563">{label}</text>'
        )
    for series_index, (label, values) in enumerate(series):
        commands: list[str] = []
        drawing = False
        for x_value, y_value in zip(x_values, values):
            if y_value is None:
                drawing = False
                continue
            command = "L" if drawing else "M"
            commands.append(f"{command}{x_position(x_value):.2f},{y_position(y_value):.2f}")
            drawing = True
        color = colors[series_index % len(colors)]
        path_commands = " ".join(commands)
        svg.append(
            f'<path d="{path_commands}" fill="none" stroke="{color}" '
            'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        legend_x = left + series_index * 190
        svg.append(
            f'<line x1="{legend_x}" y1="{height - 18}" x2="{legend_x + 22}" '
            f'y2="{height - 18}" stroke="{color}" stroke-width="3"/>'
        )
        svg.append(
            f'<text x="{legend_x + 28}" y="{height - 14}" font-family="sans-serif" '
            f'font-size="12" fill="#111827">{escape(label)}</text>'
        )
    svg.extend(
        [
            f'<text x="{left + plot_width / 2}" y="{height - 42}" text-anchor="middle" '
            f'font-family="sans-serif" font-size="12" fill="#374151">{escape(x_label)}</text>',
            f'<text x="20" y="{top + plot_height / 2}" text-anchor="middle" '
            f'transform="rotate(-90 20 {top + plot_height / 2})" font-family="sans-serif" '
            f'font-size="12" fill="#374151">{escape(y_label)}</text>',
            "</svg>",
        ]
    )
    path.write_text("\n".join(svg), encoding="utf-8")


def summary_row(analysis: PairAnalysis) -> dict[str, object]:
    observations = analysis.observations
    basis = [item.basis for item in observations]
    basis_bps = [item.basis_bps for item in observations]
    spot_returns = [
        item.spot_return_pct for item in observations if item.spot_return_pct is not None
    ]
    future_returns = [
        item.future_return_pct for item in observations if item.future_return_pct is not None
    ]
    contemporaneous = next(
        (item for item in analysis.lag_correlations if item.lag_steps == 0), None
    )
    valid_lags = [item for item in analysis.lag_correlations if item.correlation is not None]
    best = max(valid_lags, key=lambda item: abs(item.correlation or 0)) if valid_lags else None
    return {
        "spot_symbol": analysis.pair.spot_symbol,
        "future_symbol": analysis.pair.future_symbol,
        "expiration_date": analysis.pair.expiration_date,
        "observations": len(observations),
        "start_time": _timestamp_text(observations[0].timestamp_ns) if observations else "",
        "end_time": _timestamp_text(observations[-1].timestamp_ns) if observations else "",
        "mean_basis": fmean(basis) if basis else None,
        "basis_std": pstdev(basis) if len(basis) > 1 else None,
        "mean_basis_bps": fmean(basis_bps) if basis_bps else None,
        "return_correlation": (
            contemporaneous.correlation if contemporaneous is not None else None
        ),
        "best_lag_seconds": best.lag_seconds if best else None,
        "best_lag_correlation": best.correlation if best else None,
        "spot_return_volatility_pct": pstdev(spot_returns) if len(spot_returns) > 1 else None,
        "future_return_volatility_pct": (
            pstdev(future_returns) if len(future_returns) > 1 else None
        ),
    }


def write_analysis_summary(path: str | Path, analyses: list[PairAnalysis]) -> None:
    rows = [summary_row(analysis) for analysis in analyses]
    fields = list(rows[0]) if rows else ["spot_symbol", "future_symbol", "observations"]
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_rows(output_path, fields, rows)
