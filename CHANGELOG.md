# Changelog

## v0.3.1 “Adaptive Universe”

- Discover dashboard symbols directly from the currently selected SQLite database.
- Discover every analyzable spot/futures relationship stored in that database.
- Refresh pair calculations and persistent cache selection when the database changes.
- Exclude instruments without snapshots and futures whose underlying spot book is unavailable.
- Prefer the expanded BIST50 database automatically when it is present.

## v0.3.0 “Complete Horizon”

- Add the dated Q2 2026 BIST50 constituent configuration used by the April 27 capture.
- Match all captured BIST50 members with equity futures to their front-month contracts.
- Generate a reproducible 41-pair, 82-book ingestion configuration from the instrument catalog.
- Validate 65,986,550 expanded-scope snapshots in a 74.5 GB SQLite database.
- Preserve the separate personal 10-pair configuration for the required relationship analysis.
- Add assignment traceability and official source documentation for the expanded scope.

## v0.2.1 “Steady Pulse”

- Stabilize nearest-value chart hover behavior in compact layouts.
- Replace bubbling hover events with `pointermove` and non-bubbling `pointerleave`.
- Prevent the crosshair, tooltip, and highlighted points from repeatedly clearing and reappearing.
- Add regression coverage for the chart interaction event contract.

## v0.2.0 “Market Pulse”

- Deliver full-capture BIST order-book reconstruction and spot/futures analysis.
- Add the interactive Streamlit dashboard, snapshot navigation, configurable charts, and exports.
- Optimize full-database queries, browser rendering, and persistent top-of-book caching.
