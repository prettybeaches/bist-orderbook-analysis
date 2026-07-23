from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# ruff: noqa: E402


# Allow `streamlit run app.py` to work directly from a source checkout.
PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import streamlit as st

from bist_orderbook.analysis import (
    PairAnalysis,
    analyze_top_of_books,
    calculate_lag_correlations,
    load_symbol_pairs,
    summary_row,
)
from bist_orderbook.analysis_cache import load_cached_top_of_book
from bist_orderbook.dashboard import (
    analysis_csv,
    basis_chart_rows,
    database_status,
    downsample_observations,
    lag_chart_rows,
    momentum_chart_rows,
    nearest_hover_parameter,
    price_chart_rows,
    snapshot_table,
    timeline_index_at_or_before,
)
from bist_orderbook.query import SnapshotQuery, parse_time_ns, query_snapshots
from bist_orderbook.storage import SQLiteStore


st.set_page_config(page_title="BIST Order Book Analysis", page_icon="📈", layout="wide")


@st.cache_data(show_spinner=False)
def cached_pairs(path: str):
    return load_symbol_pairs(path)


@st.cache_data(show_spinner=False)
def cached_status(path: str, modified_ns: int, include_price_level_count: bool):
    del modified_ns
    return database_status(path, include_price_level_count=include_price_level_count)


@st.cache_data(
    show_spinner="Loading optimized top-of-book data...",
    max_entries=80,
)
def cached_top_of_book(
    database: str,
    database_modified_ns: int,
    order_book_id: int,
    interval_ms: int,
):
    return load_cached_top_of_book(
        database,
        database_modified_ns=database_modified_ns,
        order_book_id=order_book_id,
        interval_ms=interval_ms,
    )


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
    spot = cached_top_of_book(
        database,
        database_modified_ns,
        pair.spot_order_book_id,
        interval_ms,
    )
    future = cached_top_of_book(
        database,
        database_modified_ns,
        pair.future_order_book_id,
        interval_ms,
    )
    return analyze_top_of_books(
        pair,
        spot,
        future,
        interval_ms=interval_ms,
        max_staleness_ms=max_staleness_ms,
        momentum_periods=momentum_periods,
        max_lag_steps=max_lag_steps,
    )


def _wide_chart_rows(
    data: list[dict[str, object]], *, x: str, y: str, color: str | None, y_title: str
) -> tuple[list[dict[str, object]], list[tuple[str, str]]]:
    series_names = list(dict.fromkeys(str(row[color]) for row in data)) if color else [y_title]
    series_fields = [(name, f"value_{index}") for index, name in enumerate(series_names)]
    field_by_series = dict(series_fields)
    rows_by_x: dict[object, dict[str, object]] = {}
    for row in data:
        x_value = row[x]
        wide_row = rows_by_x.setdefault(x_value, {x: x_value})
        series_name = str(row[color]) if color else y_title
        wide_row[field_by_series[series_name]] = row[y]
    for wide_row in rows_by_x.values():
        wide_row["hover_values"] = " · ".join(
            f"{name}: {float(wide_row[field]):.6f}"
            for name, field in series_fields
            if field in wide_row
        )
    return list(rows_by_x.values()), series_fields


def line_chart(
    data,
    *,
    chart_key: str,
    x: str,
    y: str,
    color: str | None,
    y_title: str,
    height: int,
    enable_zoom: bool,
) -> None:
    if not data:
        st.info("No observations are available for the selected chart options.")
        return
    wide_rows, series_fields = _wide_chart_rows(
        data, x=x, y=y, color=color, y_title=y_title
    )
    hover_name = f"hover_{chart_key}"
    zoom_name = f"zoom_{chart_key}"
    value_fields = [field for _, field in series_fields]
    label_expression = " : ".join(
        f"datum.series_key === {json.dumps(field)} ? {json.dumps(name)}"
        for name, field in series_fields
    )
    label_expression = f"{label_expression} : datum.series_key"
    folded_transform = [
        {"fold": value_fields, "as": ["series_key", "chart_value"]},
        {"calculate": label_expression, "as": "series"},
    ]
    x_encoding = {
        "field": x,
        "type": "temporal",
        "title": "Time (UTC)",
        "axis": {"grid": True, "labelOverlap": "greedy", "tickCount": 8},
    }
    value_encoding = {
        "field": "chart_value",
        "type": "quantitative",
        "title": y_title,
        "scale": {"zero": False},
        "axis": {"grid": True},
    }
    line_encoding = {"y": value_encoding}
    point_encoding = {
        **line_encoding,
        "opacity": {
            "condition": {"param": hover_name, "empty": False, "value": 1},
            "value": 0,
        },
    }
    if color:
        color_encoding = {
            "field": "series",
            "type": "nominal",
            "title": None,
            "sort": [name for name, _ in series_fields],
        }
        line_encoding["color"] = color_encoding
        point_encoding["color"] = color_encoding
    tooltip = [
        {
            "field": x,
            "type": "temporal",
            "title": "Time",
            "format": "%Y-%m-%d %H:%M:%S",
        },
        {"field": "hover_values", "type": "nominal", "title": "Values"},
    ]
    params = []
    if enable_zoom:
        params.append(
            {
                "name": zoom_name,
                "select": {"type": "interval", "encodings": ["x"]},
                "bind": "scales",
            }
        )
    st.vega_lite_chart(
        spec={
            "data": {"values": wide_rows},
            "encoding": {"x": x_encoding},
            "layer": [
                {
                    "transform": folded_transform,
                    "params": params,
                    "mark": {"type": "line", "strokeWidth": 2},
                    "encoding": line_encoding,
                },
                {
                    "mark": {"type": "point", "opacity": 0},
                    "params": [nearest_hover_parameter(hover_name, x)],
                    "encoding": {"tooltip": tooltip},
                },
                {
                    "transform": [
                        *folded_transform,
                        {"filter": {"param": hover_name, "empty": False}},
                    ],
                    "mark": {"type": "point", "size": 60},
                    "encoding": point_encoding,
                },
                {
                    "transform": [{"filter": {"param": hover_name, "empty": False}}],
                    "mark": {"type": "rule"},
                    "encoding": {"tooltip": tooltip},
                },
            ],
            "height": height,
        },
        width="stretch",
        key=f"chart_{chart_key}",
    )


def lag_chart(data, *, height: int, fixed_scale: bool) -> None:
    if not data:
        st.info("No lead-lag observations are available.")
        return
    correlation_scale = {"domain": [-1, 1]} if fixed_scale else {"zero": True}
    x_encoding = {
        "field": "lag_seconds",
        "type": "quantitative",
        "title": "Lag (seconds)",
        "axis": {"grid": True, "tickMinStep": 1},
    }
    y_encoding = {
        "field": "correlation",
        "type": "quantitative",
        "title": "Correlation",
        "scale": correlation_scale,
        "axis": {"grid": True},
    }
    tooltip = [
        {"field": "lag_seconds", "title": "Lag (seconds)"},
        {"field": "correlation", "title": "Correlation", "format": ".6f"},
        {"field": "observations", "title": "Observations"},
    ]
    st.vega_lite_chart(
        spec={
            "data": {"values": data},
            "encoding": {"x": x_encoding},
            "layer": [
                {
                    "mark": {"type": "bar"},
                    "encoding": {"y": y_encoding, "tooltip": tooltip},
                },
                {
                    "mark": {"type": "point", "opacity": 0},
                    "params": [nearest_hover_parameter("lag_hover", "lag_seconds")],
                    "encoding": {"y": y_encoding, "tooltip": tooltip},
                },
                {
                    "transform": [{"filter": {"param": "lag_hover", "empty": False}}],
                    "mark": {"type": "rule"},
                    "encoding": {"tooltip": tooltip},
                },
            ],
            "height": height,
        },
        width="stretch",
    )


def render_book(snapshot, heading: str) -> None:
    st.subheader(heading)
    if snapshot is None:
        st.info("No snapshot is available for this instrument.")
        return
    st.caption(
        f"Sequence {snapshot.sequence_number:,} · {format_timestamp_ns(snapshot.timestamp_ns)} · "
        f"Book {snapshot.order_book_id}"
    )
    st.dataframe(snapshot_table(snapshot), hide_index=True, width="stretch")


def format_timestamp_ns(timestamp_ns: int) -> str:
    seconds, nanoseconds = divmod(timestamp_ns, 1_000_000_000)
    timestamp = datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{timestamp}.{nanoseconds:09d}Z"


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
    analysis_scope = f"{pair.name}_{interval_ms}_{max_staleness_ms}_{momentum_periods}"

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

    st.subheader("Order-book snapshots")
    snapshot_mode = st.radio(
        "Snapshot selection",
        ("Latest populated", "Latest event", "Analysis timeline"),
        horizontal=True,
        help=(
            "Latest populated skips closing book-flush events. Latest event includes empty "
            "snapshots. Analysis timeline lets you inspect both books across the trading day."
        ),
    )
    snapshot_end_ns = None
    populated_only = snapshot_mode != "Latest event"
    if snapshot_mode == "Analysis timeline":
        if analysis.observations:
            timeline_timestamps = [item.timestamp_ns for item in analysis.observations]
            timeline_scope = analysis_scope
            timeline_slider_key = f"timeline_index_{timeline_scope}"
            timeline_target_key = f"timeline_target_{timeline_scope}"
            if timeline_slider_key not in st.session_state:
                st.session_state[timeline_slider_key] = len(timeline_timestamps) - 1
            if timeline_target_key not in st.session_state:
                st.session_state[timeline_target_key] = timeline_timestamps[-1]

            search_columns = st.columns([4, 1], vertical_alignment="bottom")
            timestamp_search = search_columns[0].text_input(
                "Go to timestamp",
                placeholder="2026-04-27T15:10:00.115343327+00:00 or Unix nanoseconds",
                key=f"timeline_search_{timeline_scope}",
                help=(
                    "The books shown are the latest snapshots at or before this timestamp. "
                    "ISO-8601 values must include a UTC offset."
                ),
            )
            if search_columns[1].button(
                "Go",
                key=f"timeline_go_{timeline_scope}",
                type="primary",
                width="stretch",
            ):
                try:
                    requested_ns = parse_time_ns(timestamp_search)
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.session_state[timeline_slider_key] = timeline_index_at_or_before(
                        timeline_timestamps, requested_ns
                    )
                    st.session_state[timeline_target_key] = requested_ns

            def update_timeline_target() -> None:
                selected_index = st.session_state[timeline_slider_key]
                st.session_state[timeline_target_key] = timeline_timestamps[selected_index]

            timeline_index = st.slider(
                "Timeline position",
                min_value=0,
                max_value=len(analysis.observations) - 1,
                key=timeline_slider_key,
                on_change=update_timeline_target,
                help="Select an aligned one-second observation from the analysis.",
            )
            include_empty = st.checkbox(
                "Include empty snapshots",
                value=False,
                key=f"timeline_empty_{timeline_scope}",
                help=(
                    "When enabled, book-flush snapshots can be displayed. When disabled, "
                    "the latest earlier snapshot containing price levels is shown."
                ),
            )
            populated_only = not include_empty
            snapshot_end_ns = int(st.session_state[timeline_target_key])
            snapshot_description = "snapshots" if include_empty else "populated snapshots"
            st.caption(
                f"Showing the latest {snapshot_description} at or before "
                f"{format_timestamp_ns(snapshot_end_ns)} "
                f"(nearest timeline position {timeline_index + 1:,} of "
                f"{len(analysis.observations):,})."
            )
        else:
            st.info("No aligned observations are available for this pair.")

    store = SQLiteStore(database)
    spot_snapshot = query_snapshots(
        store,
        SnapshotQuery(
            symbol=pair.spot_symbol,
            end_ns=snapshot_end_ns,
            limit=1,
            latest=True,
            populated_only=populated_only,
        ),
    )
    future_snapshot = query_snapshots(
        store,
        SnapshotQuery(
            symbol=pair.future_symbol,
            end_ns=snapshot_end_ns,
            limit=1,
            latest=True,
            populated_only=populated_only,
        ),
    )
    book_columns = st.columns(2)
    with book_columns[0]:
        render_book(spot_snapshot[0] if spot_snapshot else None, pair.spot_symbol)
    with book_columns[1]:
        render_book(future_snapshot[0] if future_snapshot else None, pair.future_symbol)

    with st.expander("Chart display options", expanded=True):
        chart_option_columns = st.columns(3)
        price_display = chart_option_columns[0].selectbox(
            "Price measure",
            ("Normalized index", "Mid-price", "One-interval return"),
        )
        basis_display = chart_option_columns[1].selectbox(
            "Basis measure",
            ("Basis points", "Price difference"),
        )
        time_window = chart_option_columns[2].selectbox(
            "Time window",
            ("Full session", "Last 15 minutes", "Last 60 minutes", "Custom range"),
        )
        visible_series = st.multiselect(
            "Visible instruments",
            (pair.spot_symbol, pair.future_symbol),
            default=(pair.spot_symbol, pair.future_symbol),
        )
        behavior_columns = st.columns(4)
        point_limit_label = behavior_columns[0].selectbox(
            "Maximum rendered points",
            ("1,000", "2,500", "5,000", "All"),
            index=1,
            help=(
                "Limits browser rendering only. Metrics, downloads, and lead-lag calculations "
                "continue to use all observations."
            ),
        )
        chart_height = behavior_columns[1].slider("Chart height", 240, 520, 320, 20)
        enable_zoom = behavior_columns[2].checkbox(
            "Enable horizontal zoom and pan",
            value=True,
            help="Drag across a chart to zoom into a time interval.",
        )
        fixed_correlation_scale = behavior_columns[3].checkbox(
            "Fix correlation axis to −1…1",
            value=True,
        )

        visible_observations = analysis.observations
        if analysis.observations and time_window == "Last 15 minutes":
            cutoff_ns = analysis.observations[-1].timestamp_ns - 15 * 60 * 1_000_000_000
            visible_observations = tuple(
                item for item in analysis.observations if item.timestamp_ns >= cutoff_ns
            )
        elif analysis.observations and time_window == "Last 60 minutes":
            cutoff_ns = analysis.observations[-1].timestamp_ns - 60 * 60 * 1_000_000_000
            visible_observations = tuple(
                item for item in analysis.observations if item.timestamp_ns >= cutoff_ns
            )
        elif analysis.observations and time_window == "Custom range":
            range_start, range_end = st.slider(
                "Observation range",
                min_value=0,
                max_value=len(analysis.observations) - 1,
                value=(0, len(analysis.observations) - 1),
                key=f"chart_range_{analysis_scope}",
            )
            visible_observations = analysis.observations[range_start : range_end + 1]
        if visible_observations:
            st.caption(
                f"{len(visible_observations):,} visible observations · "
                f"{format_timestamp_ns(visible_observations[0].timestamp_ns)} to "
                f"{format_timestamp_ns(visible_observations[-1].timestamp_ns)}"
            )

    visible_analysis = PairAnalysis(
        pair=analysis.pair,
        observations=tuple(visible_observations),
        lag_correlations=calculate_lag_correlations(
            tuple(visible_observations),
            max_lag_steps=max_lag_steps,
            interval_seconds=interval_ms / 1_000,
        ),
    )
    point_limits = {"1,000": 1_000, "2,500": 2_500, "5,000": 5_000, "All": None}
    rendered_observations = downsample_observations(
        visible_analysis.observations,
        point_limits[point_limit_label],
    )
    chart_analysis = PairAnalysis(
        pair=visible_analysis.pair,
        observations=rendered_observations,
        lag_correlations=visible_analysis.lag_correlations,
    )
    if len(rendered_observations) < len(visible_analysis.observations):
        st.caption(
            f"Rendering {len(rendered_observations):,} of "
            f"{len(visible_analysis.observations):,} observations. "
            "Analytics and downloads remain full resolution."
        )
    price_modes = {
        "Normalized index": ("normalized", "Normalized mid-price", "Index (window start = 100)"),
        "Mid-price": ("mid_price", "Spot and futures mid-price", "Mid-price"),
        "One-interval return": ("return", "One-interval return", "Return (%)"),
    }
    price_mode, price_heading, price_y_title = price_modes[price_display]
    basis_unit = "bps" if basis_display == "Basis points" else "price"
    basis_y_title = "Basis (bps)" if basis_unit == "bps" else "Futures − spot"

    st.subheader(price_heading)
    price_rows = [
        row
        for row in price_chart_rows(chart_analysis, price_mode)
        if row["series"] in visible_series
    ]
    line_chart(
        price_rows,
        chart_key="price",
        x="time",
        y="value",
        color="series",
        y_title=price_y_title,
        height=chart_height,
        enable_zoom=enable_zoom,
    )

    chart_columns = st.columns(2)
    with chart_columns[0]:
        st.subheader("Futures basis")
        line_chart(
            basis_chart_rows(chart_analysis, basis_unit),
            chart_key="basis",
            x="time",
            y="value",
            color=None,
            y_title=basis_y_title,
            height=chart_height,
            enable_zoom=enable_zoom,
        )
    with chart_columns[1]:
        st.subheader("Momentum")
        momentum_rows = [
            row
            for row in momentum_chart_rows(chart_analysis)
            if row["series"] in visible_series
        ]
        line_chart(
            momentum_rows,
            chart_key="momentum",
            x="time",
            y="value",
            color="series",
            y_title="Momentum (%)",
            height=chart_height,
            enable_zoom=enable_zoom,
        )

    st.subheader("Lead-lag return correlation")
    st.caption(
        "Positive lag tests whether futures lead spot; negative lag tests whether spot leads."
    )
    lag_chart(
        lag_chart_rows(visible_analysis),
        height=chart_height,
        fixed_scale=fixed_correlation_scale,
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
    populated_only = st.checkbox(
        "Only snapshots with price levels",
        value=False,
        help="Exclude empty snapshots created by order-book flush events.",
    )
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
                populated_only=populated_only,
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
    include_price_levels = st.checkbox(
        "Calculate exact price-level count",
        value=False,
        help=(
            "This scans the complete price-level table and can take a while on the full database."
        ),
    )
    status = cached_status(
        str(database),
        database.stat().st_mtime_ns,
        include_price_levels,
    )
    metrics = st.columns(3)
    metrics[0].metric("Instruments", f"{status.instrument_count:,}")
    metrics[1].metric("Snapshots", f"{status.snapshot_count:,}")
    metrics[2].metric(
        "Price levels",
        "Not calculated"
        if status.price_level_count is None
        else f"{status.price_level_count:,}",
    )
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
