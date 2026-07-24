# Full-Capture Validation

## Expanded BIST50 front-month validation

Release v0.3.0 expands reconstruction from the personal 10-pair analysis set to every Q2 2026
BIST50 constituent with an equity futures contract in the captured instrument catalog. The dated
scope resolves to 41 spot/futures pairs and 82 order books.

The complete archive was replayed into `data/processed/orderbook-bist50-full.db`:

```bash
PYTHONPATH=src python -m bist_orderbook ingest \
  data/raw/itch-pri-20260427.tar.xz \
  --pairs config/bist50_front_month_20260427.csv \
  --catalog data/processed/instruments.csv \
  --database data/processed/orderbook-bist50-full.db \
  --snapshot-every 1 \
  --batch-size 5000
```

| Measurement | Result |
| --- | ---: |
| Capture packets | 112,943,179 |
| Selected ITCH messages | 66,018,658 |
| Reconstructed snapshots | 65,986,550 |
| Selected instruments | 82 |
| Equity instruments | 41 |
| Futures instruments | 41 |
| Database size | 74,492,702,720 bytes |
| Decode errors | 0 |
| Replayed messages | 0 |
| Sequence gaps | 11 |
| Missing MoldUDP64 messages | 119,690 |
| Rejected book events | 247 |

The expanded snapshot range is `2026-04-27T04:30:00.099169+00:00` through
`2026-04-27T15:17:23.220204+00:00`. All 82 configured instruments have stored snapshots.

Post-run validation results:

- SQLite `PRAGMA quick_check` returns `ok`.
- SQLite `PRAGMA foreign_key_check` returns zero violations.
- All 36 automated tests pass.
- Ruff reports no violations.

The sequence gaps and rejected book events remain explicit feed-quality limitations. The larger
book-error count relative to the personal 10-pair replay reflects the broader 82-book scope; no
decoder errors were recorded.

## Personal 10-pair analysis validation

## Run configuration

The complete `itch-pri-20260427.tar.xz` archive was replayed without a packet or snapshot limit.
Every selected order-book event was persisted, using batches of 5,000 snapshots:

```bash
PYTHONPATH=src python -m bist_orderbook ingest \
  data/raw/itch-pri-20260427.tar.xz \
  --pairs config/symbol_pairs.csv \
  --catalog data/processed/instruments.csv \
  --database data/processed/orderbook-full.db \
  --snapshot-every 1 \
  --batch-size 5000
```

The replay took 10,912 seconds (approximately 3 hours and 2 minutes) on the development machine.

## Ingestion results

| Measurement | Result |
| --- | ---: |
| Capture packets | 112,943,179 |
| Selected ITCH messages | 23,564,861 |
| Reconstructed snapshots | 23,551,422 |
| Stored price levels | 470,368,436 |
| Selected instruments | 20 |
| Database size | 26,276,270,080 bytes |
| Decode errors | 0 |
| Replayed messages | 0 |
| Sequence gaps | 11 |
| Missing MoldUDP64 messages | 119,690 |
| Rejected book events | 123 |

The stored snapshot range is `2026-04-27T04:30:00.099169+00:00` through
`2026-04-27T15:15:34.259971+00:00`. Equity snapshots cover the continuous equity session from
approximately 06:40 through 15:10 UTC. Futures data begins at approximately 04:30 UTC and extends
through 15:10–15:15 UTC, depending on the instrument.

All 20 configured instruments have snapshots in the full database. SQLite constraints prevented
duplicate `(order_book_id, sequence_number)` snapshots, invalid sides, invalid depth levels, and
negative quantities or order counts.

## Feed-quality findings

The replay detected 11 MoldUDP64 sequence gaps containing 119,690 missing messages. The order-book
engine also rejected 123 events that violated its state invariants. These are recorded as quality
findings rather than silently repaired. Missing feed messages may contribute to later state
inconsistencies, but the current counters do not establish a one-to-one causal relationship.

The gap and rejected-event counts should therefore be included as limitations in any interpretation
of the results. The decoder itself produced zero errors, so these findings concern capture
continuity and book state rather than unsupported binary message formats.

## Full-day pair analysis

The full database produced four CSV reports and four SVG charts for each configured pair. Outputs
are stored under `reports/analysis-full/`, with aggregate metrics in `summary.csv`.

| Spot | Future | Aligned observations | Mean basis (bps) | Return correlation | Best lag (s) |
| --- | --- | ---: | ---: | ---: | ---: |
| AKBNK.E | F_AKBNK0426 | 28,915 | 62.94 | 0.658 | 0 |
| ASELS.E | F_ASELS0426 | 28,921 | 60.30 | 0.800 | 0 |
| BIMAS.E | F_BIMAS0426 | 28,803 | 55.98 | 0.610 | 0 |
| EREGL.E | F_EREGL0426 | 28,785 | 62.67 | 0.729 | 0 |
| GARAN.E | F_GARAN0426 | 28,755 | 61.99 | 0.599 | 0 |
| ISCTR.E | F_ISCTR0426 | 28,915 | 59.48 | 0.534 | 0 |
| KCHOL.E | F_KCHOL0426 | 28,600 | 61.45 | 0.676 | 0 |
| SISE.E | F_SISE0426 | 28,913 | 63.58 | 0.724 | 0 |
| THYAO.E | F_THYAO0426 | 28,917 | 55.97 | 0.587 | 0 |
| TUPRS.E | F_TUPRS0426 | 28,880 | 62.12 | 0.807 | 0 |

The default analysis uses one-second alignment, a maximum quote age of five seconds, five-period
momentum, and lead-lag correlations from -5 through +5 seconds. The strongest absolute return
correlation for every selected pair occurs at zero seconds under this configuration.

## Deliverable checks

- 41 non-empty CSV files: four per pair plus the aggregate summary.
- 40 non-empty SVG charts: four per pair.
- All SVG files parse as valid XML.
- All 10 configured pairs appear in the summary.
- SQLite `PRAGMA quick_check` returns `ok`.
- SQLite reports zero foreign-key violations.
- All generated reports remain non-empty and valid; current automated tests and Ruff checks pass.
