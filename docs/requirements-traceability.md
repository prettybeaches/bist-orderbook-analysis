# Assignment Requirements Traceability

## Scope decision

The capture date is April 27, 2026, so index membership is evaluated for the BIST50 period from
April 1 through June 30, 2026.

The official Borsa Istanbul announcement for that period adds `CANTE` and `TURSG` to BIST50 and
removes `DOHOL` and `SOKM`:

<https://www.borsaistanbul.com/en/announcement/15392/borsa-istanbul-announces-constituent-changes-bist-stock-indices-second-quarter-2026>

The official July 2026 review provides the next-period changes used to verify the period boundary:

<https://www.borsaistanbul.com/en/announcement/15483/bist-stock-indices-periodic-review>

`config/bist50_2026_q2.csv` records the resulting 50 constituents and their validity dates. The
`build-pairs` command intersects that dated list with `data/processed/instruments.csv` and selects
the first captured equity futures expiry that is not earlier than the capture date.

For April 27, the result is:

- 50 BIST50 constituents
- 41 constituents with captured equity futures
- 9 constituents without a captured equity futures contract
- 41 front-month spot/futures pairs
- 82 order books selected for reconstruction
- 65,986,550 reconstructed snapshots across all 82 selected books
- SQLite integrity check passed with zero foreign-key violations

The unavailable constituents are `BTCIM.E`, `CANTE.E`, `CCOLA.E`, `DSTKF.E`, `KUYAS.E`, `MAVI.E`,
`MIATK.E`, `PASEU.E`, and `TURSG.E`.

The separate `config/symbol_pairs.csv` file remains the personal 10-pair selection used for the
required relationship analysis.

## Requirement map

| Assignment requirement | Implementation and evidence |
| --- | --- |
| Reconstruct a 10-level order book and price table from the supplied PCAP | Streaming capture reader, MoldUDP64 and BISTECH ITCH decoders, 10-level order-book engine, and SQLite price levels constrained to depths 1–10 |
| Cover BIST50 symbols with captured futures and the corresponding futures | Dated constituent file plus generated `config/bist50_front_month_20260427.csv`, containing 41 eligible pairs and 82 books |
| Store price tables in a database | Indexed SQLite `snapshots` and `price_levels` tables |
| Query by time, sequence number, order-book ID, and symbol | `bist-orderbook query` and the dashboard snapshot query |
| Report spot/futures price, momentum, and time relationships | Price, spread, basis, return, momentum, correlation, and lead-lag reports |
| Analyze 10 different spot/futures pairs | `config/symbol_pairs.csv` contains 10 personal pairs |
| Produce a separate CSV result and graph for every analysis | Four CSV files and four SVG charts per pair, plus an aggregate summary |
| Keep personally written code on GitHub | Source, tests, configuration, and documentation are versioned; large raw and generated artifacts remain excluded |

## Data-quality limitations

The full capture contains 11 MoldUDP64 sequence gaps representing 119,690 missing messages and 123
rejected book events. These findings are recorded rather than silently repaired. See
`docs/full-capture-validation.md` for the measured results and database integrity checks.
