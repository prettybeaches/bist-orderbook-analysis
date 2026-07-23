# Analysis Methodology

## Input

The analysis reads reconstructed 10-level snapshots from SQLite. Only snapshots containing both a
level-one bid and a level-one ask are eligible for alignment.

## Time alignment

Spot and futures books update at different times. The application creates a regular time grid and
uses the most recent observation at or before each grid timestamp. An observation is discarded if
either book is older than the configured maximum staleness.

Defaults:

- Alignment interval: 1 second
- Maximum staleness: 5 seconds
- Momentum lookback: 5 aligned periods
- Lead-lag range: -5 to +5 aligned periods

All alignment and database comparisons use Unix nanoseconds. ISO-8601 timestamps are included for
readability.

## Metrics

For best bid `bid` and best ask `ask`:

```text
mid price = (bid + ask) / 2
quoted spread = ask - bid
```

For spot mid-price `S` and futures mid-price `F`:

```text
basis = F - S
basis (bps) = (F - S) / S * 10,000
```

Returns and momentum are simple percentage changes:

```text
return[t] = (mid[t] / mid[t-1] - 1) * 100
momentum[t] = (mid[t] / mid[t-lookback] - 1) * 100
```

Lead-lag analysis uses Pearson correlation between aligned spot and futures returns. A positive lag
means futures returns are shifted earlier and therefore tests whether futures lead spot. A negative
lag tests whether spot leads futures. Lag zero is contemporaneous correlation.

## Outputs

Each configured pair receives:

- `price.csv` and `price.svg`: bid, ask, mid-price, spread, and normalized price comparison
- `basis.csv` and `basis.svg`: absolute and basis-point futures premium/discount
- `momentum.csv` and `momentum.svg`: interval returns and rolling momentum
- `lead_lag.csv` and `lead_lag.svg`: correlation by lag

`summary.csv` combines observation counts and primary metrics across all pairs.

## Interpretation limits

The current `orderbook-balanced.db` is a bounded, sampled validation database. Its results verify
the pipeline but should not be treated as full-day market conclusions. Final reporting must use a
complete ingestion run and must review any sequence gaps, stale intervals, auction states, contract
roll effects, and transaction-cost assumptions.
