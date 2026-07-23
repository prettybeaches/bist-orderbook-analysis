from __future__ import annotations

import sys
from pathlib import Path

# ruff: noqa: E402


# Allow `streamlit run app.py` to work directly from a source checkout.
PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import streamlit as st

from bist_orderbook.analysis import analyze_pair, load_symbol_pairs, summary_row
from bist_orderbook.dashboard import (
    analysis_csv,
    basis_chart_rows,
    database_status,
    lag_chart_rows,
    momentum_chart_rows,
    price_chart_rows,
    snapshot_table,
)
from bist_orderbook.query import SnapshotQuery, parse_time_ns, query_snapshots
from bist_orderbook.storage import SQLiteStore


st.set_page_config(page_title="BIST Order Book Analysis", page_icon="📈", layout="wide")


@st.cache_data(show_spinner=False)
def cached_pairs(path: str):
    return load_symbol_pairs(path)


@st.cache_data(show_spinner=False)
def cached_status(path: str, modified_ns: int):
    del modified_ns
    return database_status(path)


@st.cache_data(show_spinner="Aligning spot and futures books...")
def cached_analysis(
    database: str,
    database_modified_ns: int,
    pair,
    interval_ms: int,
    max_staleness_ms: int,
    momentum_periods: int,
    max_lag_steps: int,
):
    del database_modified_ns
    return analyze_pair(
        SQLiteStore(database),
        pair,
        interval_ms=interval_ms,
        max_staleness_ms=max_staleness_ms,
        momentum_periods=momentum_periods,
        max_lag_steps=max_lag_steps,
    )


def line_chart(data, *, x: str, y: str, color: str | None, y_title: str) -> None:
    encoding = {
        "x": {"field": x, "type": "temporal", "title": "Time (UTC)"},
        "y": {"field": y, "type": "quantitative", "title": y_title, "scale": {"zero": False}},
        "tooltip": [
            {"field": x, "type": "temporal", "title": "Time"},
            {"field": y, "type": "quantitative", "title": y_title, "format": ".4f"},
        ],
    }
    if color:
        encoding["color"] = {"field": color, "type": "nominal", "title": None}
        encoding["tooltip"].insert(1, {"field": color, "type": "nominal", "title": "Series"})
    st.vega_lite_chart(
        spec={
            "data": {"values": data},
            "mark": {"type": "line", "strokeWidth": 2},
            "encoding": encoding,
            "height": 300,
        },
        width="stretch",
    )


def render_book(snapshot, heading: str) -> None:
    st.subheader(heading)
    if snapshot is None:
        st.info("No snapshot is available for this instrument.")
        return
    st.caption(
        f"Sequence {snapshot.sequence_number:,} · {snapshot.timestamp.isoformat()} · "
        f"Book {snapshot.order_book_id}"
    )
    st.dataframe(snapshot_table(snapshot), hide_index=True, width="stretch")


def render_dashboard(database: Path, pairs_path: Path) -> None:
    pairs = cached_pairs(str(pairs_path))
    pair_labels = {f"{pair.spot_symbol} / {pair.future_symbol}": pair for pair in pairs}
    selected_label = st.sidebar.selectbox("Spot / futures pair", pair_labels)
    pair = pair_labels[selected_label]
    interval_ms = st.sidebar.select_slider(
        "Alignment interval",
        options=[100, 250, 500, 1_000, 2_000, 5_000],
        value=1_000,
        format_func=lambda value: f"{value / 1000:g} s",
    )
    max_staleness_ms = st.sidebar.select_slider(
        "Maximum quote staleness",
        options=[1_000, 2_000, 5_000, 10_000, 30_000],
        value=5_000,
        format_func=lambda value: f"{value / 1000:g} s",
    )
    momentum_periods = st.sidebar.slider("Momentum periods", 1, 30, 5)
    max_lag_steps = st.sidebar.slider("Maximum lead-lag steps", 1, 20, 5)

    modified_ns = database.stat().st_mtime_ns
    analysis = cached_analysis(
        str(database),
        modified_ns,
        pair,
        interval_ms,
        max_staleness_ms,
        momentum_periods,
        max_lag_steps,
    )
    summary = summary_row(analysis)
    metric_columns = st.columns(3)
    metric_columns[0].metric("Aligned observations", f"{len(analysis.observations):,}")
    metric_columns[1].metric(
        "Mean basis",
        "—" if summary["mean_basis_bps"] is None else f'{summary["mean_basis_bps"]:.2f} bps',
    )
    metric_columns[2].metric(
        "Return correlation",
        "—" if summary["return_correlation"] is None else f'{summary["return_correlation"]:.3f}',
    )

    store = SQLiteStore(database)
    spot_snapshot = query_snapshots(
        store, SnapshotQuery(symbol=pair.spot_symbol, limit=1, latest=True)
    )
    future_snapshot = query_snapshots(
        store, SnapshotQuery(symbol=pair.future_symbol, limit=1, latest=True)
    )
    book_columns = st.columns(2)
    with book_columns[0]:
        render_book(spot_snapshot[0] if spot_snapshot else None, pair.spot_symbol)
    with book_columns[1]:
        render_book(future_snapshot[0] if future_snapshot else None, pair.future_symbol)

    st.subheader("Normalized mid-price")
    line_chart(
        price_chart_rows(analysis),
        x="time",
        y="value",
        color="series",
        y_title="Index (first observation = 100)",
    )

    chart_columns = st.columns(2)
    with chart_columns[0]:
        st.subheader("Futures basis")
        line_chart(
            basis_chart_rows(analysis),
            x="time",
            y="basis_bps",
            color=None,
            y_title="Basis (bps)",
        )
    with chart_columns[1]:
        st.subheader("Momentum")
        line_chart(
            momentum_chart_rows(analysis),
            x="time",
            y="value",
            color="series",
            y_title="Momentum (%)",
        )

    st.subheader("Lead-lag return correlation")
    st.caption(
        "Positive lag tests whether futures lead spot; negative lag tests whether spot leads."
    )
    st.vega_lite_chart(
        spec={
            "data": {"values": lag_chart_rows(analysis)},
            "mark": {"type": "bar"},
            "encoding": {
                "x": {
                    "field": "lag_seconds",
                    "type": "quantitative",
                    "title": "Lag (seconds)",
                },
                "y": {
                    "field": "correlation",
                    "type": "quantitative",
                    "title": "Correlation",
                },
                "tooltip": [
                    {"field": "lag_seconds", "title": "Lag (seconds)"},
                    {"field": "correlation", "format": ".4f"},
                    {"field": "observations"},
                ],
            },
            "height": 260,
        },
        width="stretch",
    )

    st.subheader("Downloads")
    download_columns = st.columns(4)
    for column, report in zip(download_columns, ("price", "basis", "momentum", "lead_lag")):
        column.download_button(
            f"Download {report.replace('_', '-')} CSV",
            analysis_csv(analysis, report),
            file_name=f"{pair.name}_{report}.csv",
            mime="text/csv",
            width="stretch",
        )


def optional_integer(value: str, label: str) -> int | None:
    if not value.strip():
        return None
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{label} must be an integer") from error


def render_query(database: Path, pairs_path: Path) -> None:
    pairs = cached_pairs(str(pairs_path))
    symbols = sorted(
        {symbol for pair in pairs for symbol in (pair.spot_symbol, pair.future_symbol)}
    )
    symbol = st.selectbox("Symbol", ["All symbols", *symbols])
    filter_columns = st.columns(2)
    order_book_text = filter_columns[0].text_input("Order Book ID")
    sequence_text = filter_columns[1].text_input("Sequence number")
    time_columns = st.columns(2)
    start = time_columns[0].text_input("Start time", placeholder="2026-04-27T06:40:00+00:00")
    end = time_columns[1].text_input("End time", placeholder="2026-04-27T07:00:00+00:00")
    latest = st.checkbox("Newest first", value=True)
    limit = st.slider("Maximum snapshots", 1, 50, 5)
    if st.button("Run query", type="primary"):
        try:
            query = SnapshotQuery(
                symbol=None if symbol == "All symbols" else symbol,
                order_book_id=optional_integer(order_book_text, "Order Book ID"),
                sequence_number=optional_integer(sequence_text, "Sequence number"),
                start_ns=parse_time_ns(start) if start else None,
                end_ns=parse_time_ns(end) if end else None,
                limit=limit,
                latest=latest,
            )
            snapshots = query_snapshots(SQLiteStore(database), query)
        except (OSError, ValueError) as error:
            st.error(str(error))
            return
        if not snapshots:
            st.info("No snapshots matched the selected filters.")
        for snapshot in snapshots:
            render_book(snapshot, snapshot.symbol)


def render_status(database: Path) -> None:
    status = cached_status(str(database), database.stat().st_mtime_ns)
    metrics = st.columns(3)
    metrics[0].metric("Instruments", f"{status.instrument_count:,}")
    metrics[1].metric("Snapshots", f"{status.snapshot_count:,}")
    metrics[2].metric("Price levels", f"{status.price_level_count:,}")
    st.table(
        [
            {"Property": "Database", "Value": str(database)},
            {"Property": "Size", "Value": f"{status.database_size_bytes / 1_048_576:.2f} MB"},
            {"Property": "First snapshot", "Value": status.first_timestamp or "—"},
            {"Property": "Last snapshot", "Value": status.last_timestamp or "—"},
        ]
    )


st.title("BIST Order Book Analysis")
st.caption("10-level equity and futures order books reconstructed from BISTECH ITCH market data")

database_candidates = (
    Path("data/processed/orderbook-full.db"),
    Path("data/processed/orderbook-balanced.db"),
    Path("data/processed/orderbook.db"),
)
default_database = next((path for path in database_candidates if path.exists()), database_candidates[-1])
database = Path(st.sidebar.text_input("SQLite database", str(default_database)))
pairs_path = Path(st.sidebar.text_input("Pair configuration", "config/symbol_pairs.csv"))
page = st.sidebar.radio("View", ("Pair dashboard", "Snapshot query", "Data status"))

if not database.exists():
    st.error(f"Database not found: {database}")
elif not pairs_path.exists():
    st.error(f"Pair configuration not found: {pairs_path}")
elif page == "Pair dashboard":
    render_dashboard(database, pairs_path)
elif page == "Snapshot query":
    render_query(database, pairs_path)
else:
    render_status(database)
