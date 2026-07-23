# Dashboard Performance

Performance measurements use `data/processed/orderbook-full.db`, containing 23,551,422 snapshots,
with the default one-second alignment configuration.

## Improvements

- Replace the original all-level grouped query with indexed level-1 bid/ask lookups.
- Select the last valid top-of-book event inside SQLite for each alignment bucket.
- Cache sampled top-of-book rows independently by database, book ID, and interval.
- Persist the cache as compressed CSV under `data/processed/.cache/`.
- Render at most 2,500 chart observations by default while retaining full-resolution analytics.
- Use indexed snapshot metadata for the Data Status page.
- Calculate the exact price-level count only when explicitly requested.

## Measurements

| Operation | Before | After |
| --- | ---: | ---: |
| AKBNK equity top-of-book query | 61.4 s | about 9 s on a cache miss |
| Complete AKBNK pair load and analysis | 84.1 s | 21.2 s on a cache miss |
| Complete AKBNK pair from persistent cache | — | 0.34 s |
| Persistent cache read only | — | 0.11 s |
| Default Data Status query | 14.4 s | 0.003 s |
| Price-chart rows sent to the browser | 57,830 | 5,000 |
| Price-chart JSON payload | 5.27 MB | 0.46 MB |

The default caches for all 20 configured instruments occupy approximately 5.4 MB.

## Correctness

The optimized AKBNK result matches the full-resolution baseline:

- 28,915 aligned observations
- Start: `2026-04-27T06:55:12.000000000Z`
- End: `2026-04-27T15:09:59.000000000Z`
- Mean basis: `62.941764321066245` bps
- Return correlation: `0.6580576346668355`
- Best lag: `0` seconds

Display downsampling affects only browser chart marks. Summary metrics, lead-lag calculations,
snapshot queries, downloads, and generated reports continue to use all aligned observations.
