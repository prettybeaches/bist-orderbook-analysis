# BIST Order Book Analysis

A PCAP-based data pipeline that replays BIST market data, reconstructs 10-level order books for
equity and derivatives instruments, stores queryable snapshots, and analyzes related spot/futures
pairs.

Current release: **v0.3.0 “Complete Horizon”**

## Project scope

- Parse BIST ITCH messages carried in PCAP packets.
- Build 10-level bid/ask order books for BIST 50 equities and related futures contracts.
- Store data that can be queried by time, sequence number, order book ID, and symbol.
- Analyze price, returns, momentum, basis/spread, and lead-lag relationships for at least 10
  spot/futures pairs.
- Produce a CSV result and chart for every analysis.

## Architecture

```text
PCAP -> packet reader -> protocol decoder -> normalized events
     -> order book engine -> 10-level snapshots -> SQLite
     -> pair matching and analysis -> CSV + charts
```

The decoder is isolated behind a protocol boundary so feed-specific parsing can evolve without
changing the order book, storage, or analysis layers.

The implemented binary fields follow the official Borsa Istanbul
[BISTECH ITCH Protocol Specification](https://borsaistanbul.com/files/bistech-itch-protocol-specification.pdf)
(Version 2112). The transport layer is decoded as MoldUDP64.

## Quick start

Python 3.11 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install '.[dev,pcap,analysis,ui]'
bist-orderbook init-db --database data/processed/orderbook.db
pytest
```

The database can also be initialized with only the Python standard library and without installing
the package:

```bash
PYTHONPATH=src python -m bist_orderbook init-db \
  --database data/processed/orderbook.db
```

Inspect the first 1,000 packets without extracting the large archive:

```bash
PYTHONPATH=src python -m bist_orderbook inspect-pcap \
  data/raw/itch-pri-20260427.tar.xz --limit 1000 --samples 10
```

Discover instrument directory messages and export a catalog:

```bash
PYTHONPATH=src python -m bist_orderbook list-instruments \
  data/raw/itch-pri-20260427.tar.xz \
  --output data/processed/instruments.csv \
  --limit 8000000
```

The personal 10 spot/front-month futures selections used for relationship analysis are stored in
`config/symbol_pairs.csv`.

Generate the complete front-month ingestion scope for BIST50 constituents valid on the capture
date:

```bash
PYTHONPATH=src python -m bist_orderbook build-pairs \
  --constituents config/bist50_2026_q2.csv \
  --catalog data/processed/instruments.csv \
  --as-of 2026-04-27 \
  --output config/bist50_front_month_20260427.csv
```

This produces 41 eligible spot/futures pairs, or 82 selected order books. Nine Q2 2026 BIST50
members do not have an equity futures contract in the captured instrument catalog. See
`docs/requirements-traceability.md` for the dated scope and official sources.

Replay the complete 82-book scope and persist every event snapshot:

```bash
PYTHONPATH=src python -m bist_orderbook ingest \
  data/raw/itch-pri-20260427.tar.xz \
  --pairs config/bist50_front_month_20260427.csv \
  --catalog data/processed/instruments.csv \
  --database data/processed/orderbook-bist50-full.db
```

For a smaller sampled database, use `--snapshot-every 100`. Bounded validation runs can also use
`--limit` or `--max-snapshots`.

Query the latest 10-level snapshot by symbol:

```bash
PYTHONPATH=src python -m bist_orderbook query \
  --database data/processed/orderbook.db \
  --symbol ASELS.E --latest --limit 1
```

Filters can be combined with `--order-book-id`, `--sequence`, `--start`, and `--end`. Time values
accept Unix nanoseconds or offset-aware ISO-8601 strings. Add `--output reports/query.csv` to export
the matching levels.

Generate price, basis, momentum, and lead-lag reports for all configured pairs:

```bash
PYTHONPATH=src python -m bist_orderbook analyze \
  --database data/processed/orderbook.db \
  --pairs config/symbol_pairs.csv \
  --output reports/analysis
```

Each pair receives four CSV reports and four SVG charts. The default alignment interval is one
second, the maximum quote staleness is five seconds, momentum uses five aligned observations, and
lead-lag correlations cover -5 through +5 seconds. See `docs/analysis-methodology.md` for definitions.

Launch the interactive dashboard:

```bash
streamlit run app.py
```

The dashboard prefers `data/processed/orderbook-full.db`, then falls back to the balanced sample and
`data/processed/orderbook.db`. It provides a spot/futures analysis view, an interactive snapshot
query, database status metrics, and CSV downloads. The pair view can display the latest populated
books, the absolute latest events (including closing flushes), or synchronized books selected from
the analysis timeline. The timeline accepts exact ISO-8601 or Unix-nanosecond timestamp searches
and can include empty book-flush snapshots. Chart controls allow users to select the price and basis
measures, visible instruments, time window, chart height, zoom behavior, and correlation scale.
Moving the pointer anywhere across a chart shows the nearest timestamp and values. Database and
pair-configuration paths can be changed in the sidebar.

For responsive exploration of the full database, top-of-book rows are reduced inside SQLite at the
selected alignment interval and stored in `data/processed/.cache/`. Cache keys include the database
size and modification time, so regenerated databases are loaded into new cache entries
automatically. Charts render at most 2,500 points by default; this display-only limit does not
change metrics, lead-lag calculations, or CSV downloads. The Data Status page defers the expensive
exact price-level count until explicitly requested.

Measured before/after timings and correctness checks are documented in
`docs/performance.md`.

## Roadmap

1. Validate PCAP transport and BIST feed framing.
2. Decode message types into normalized market events and cover them with fixture tests.
3. Produce deterministic 10-level books from add, execute, delete, and flush events.
4. Persist snapshots to SQLite in batches with repeatable ingestion.
5. Configure spot/futures symbol pairs and generate analysis reports.
6. Explore reconstructed books and pair analysis through a Streamlit UI.
7. Validate correctness, sequence gaps, and performance on the complete capture.

## Versioning

Releases follow semantic versioning and use a memorable release name:

```text
vMAJOR.MINOR.PATCH “Version Name” — Short Release Summary
```

Patch releases retain a focused name that describes the improvement or fix.

## Data layout

- `data/raw/`: PCAP and reference inputs; excluded from Git.
- `data/processed/`: generated SQLite databases; excluded from Git.
- `reports/`: generated CSV files and charts; excluded from Git.

## Current status

Implemented:

- Streaming `.tar.xz` and classic-PCAP reading without extracting the 18.5 GB capture.
- Ethernet, IPv4, and UDP parsing.
- MoldUDP64 session, sequence, heartbeat, and message framing.
- Core BISTECH ITCH 2112 message decoding (`T`, `R`, `A`, `E`, `C`, `D`, and `Y`).
- Multi-channel instrument discovery with sequence-gap and replay detection.
- A real-data instrument catalog and an editable 10-pair configuration.
- A dated Q2 2026 BIST50 scope and reproducible 41-pair front-month ingestion configuration.
- Protocol-independent event models and a 10-level order book engine.
- Multicast-only selected-book replay with exact nanosecond timestamps.
- Transactional SQLite batch writes for snapshots and their 10-level price tables.
- An indexed SQLite schema supporting time, sequence, book ID, and symbol access paths.
- Composable snapshot queries with terminal tables and optional CSV export.
- Spot/futures price, spread, basis, return, momentum, and lead-lag analysis.
- Separate CSV and SVG outputs for every pair and analysis category.
- A Streamlit dashboard with pair charts, 10-level book tables, snapshot queries, data status, and
  CSV downloads.
- Complete-capture validation over 112.9 million packets and 66.0 million expanded-scope
  reconstructed snapshots.

Full-capture measurements and known feed gaps are documented in
`docs/full-capture-validation.md`.
