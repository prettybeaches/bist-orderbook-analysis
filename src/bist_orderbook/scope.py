from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from bist_orderbook.analysis import SymbolPair


@dataclass(frozen=True, slots=True)
class ScopeResult:
    constituent_count: int
    pairs: tuple[SymbolPair, ...]
    unavailable_symbols: tuple[str, ...]


def build_front_month_scope(
    constituents_path: str | Path,
    catalog_path: str | Path,
    *,
    as_of: date,
) -> ScopeResult:
    """Match index constituents to their first unexpired equity futures contract."""
    constituents = _load_constituents(constituents_path, as_of=as_of)
    with Path(catalog_path).open(encoding="utf-8", newline="") as source:
        catalog = list(csv.DictReader(source))

    cash_by_symbol = {
        row["symbol"]: row for row in catalog if row["market"] == "CASH"
    }
    futures_by_underlying: dict[str, list[dict[str, str]]] = {}
    for row in catalog:
        if row["market"] != "FUTURE" or not row["underlying_symbol"]:
            continue
        expiration = _parse_catalog_date(row["expiration_date"])
        if expiration < as_of:
            continue
        futures_by_underlying.setdefault(row["underlying_symbol"], []).append(row)

    pairs: list[SymbolPair] = []
    unavailable: list[str] = []
    for symbol in constituents:
        spot = cash_by_symbol.get(symbol)
        futures = futures_by_underlying.get(symbol, [])
        if spot is None or not futures:
            unavailable.append(symbol)
            continue
        future = min(
            futures,
            key=lambda row: (_parse_catalog_date(row["expiration_date"]), row["symbol"]),
        )
        pairs.append(
            SymbolPair(
                spot_symbol=symbol,
                spot_order_book_id=int(spot["order_book_id"]),
                future_symbol=future["symbol"],
                future_order_book_id=int(future["order_book_id"]),
                expiration_date=future["expiration_date"],
            )
        )

    return ScopeResult(
        constituent_count=len(constituents),
        pairs=tuple(pairs),
        unavailable_symbols=tuple(unavailable),
    )


def write_symbol_pairs(path: str | Path, pairs: tuple[SymbolPair, ...]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "spot_symbol",
        "spot_order_book_id",
        "future_symbol",
        "future_order_book_id",
        "expiration_date",
    ]
    with output.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        for pair in pairs:
            writer.writerow(
                {
                    "spot_symbol": pair.spot_symbol,
                    "spot_order_book_id": pair.spot_order_book_id,
                    "future_symbol": pair.future_symbol,
                    "future_order_book_id": pair.future_order_book_id,
                    "expiration_date": pair.expiration_date,
                }
            )


def _load_constituents(path: str | Path, *, as_of: date) -> tuple[str, ...]:
    with Path(path).open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    if not rows:
        raise ValueError("constituent configuration is empty")

    symbols: set[str] = set()
    for row in rows:
        valid_from = date.fromisoformat(row["valid_from"])
        valid_to = date.fromisoformat(row["valid_to"])
        if valid_from <= as_of <= valid_to:
            symbols.add(row["symbol"])
    if not symbols:
        raise ValueError(f"no constituents are valid on {as_of.isoformat()}")
    return tuple(sorted(symbols))


def _parse_catalog_date(value: str) -> date:
    if len(value) != 8 or not value.isdigit():
        raise ValueError(f"invalid catalog expiration date: {value!r}")
    return date(int(value[:4]), int(value[4:6]), int(value[6:]))
