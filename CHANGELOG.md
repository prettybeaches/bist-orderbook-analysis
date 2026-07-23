# Changelog

## v0.2.1 “Steady Pulse”

- Stabilize nearest-value chart hover behavior in compact layouts.
- Replace bubbling hover events with `pointermove` and non-bubbling `pointerleave`.
- Prevent the crosshair, tooltip, and highlighted points from repeatedly clearing and reappearing.
- Add regression coverage for the chart interaction event contract.

## v0.2.0 “Market Pulse”

- Deliver full-capture BIST order-book reconstruction and spot/futures analysis.
- Add the interactive Streamlit dashboard, snapshot navigation, configurable charts, and exports.
- Optimize full-database queries, browser rendering, and persistent top-of-book caching.
