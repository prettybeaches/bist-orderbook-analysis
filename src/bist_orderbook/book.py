from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from bist_orderbook.domain import BookEvent, BookSnapshot, EventType, PriceLevel, Side
from bist_orderbook.itch import BookFlush


@dataclass(slots=True)
class _Order:
    side: Side
    price: Decimal
    quantity: int


class OrderBook:
    """Deterministic price-time-agnostic L2 book reconstructed from order events."""

    def __init__(self, order_book_id: int, symbol: str, depth: int = 10) -> None:
        if depth <= 0:
            raise ValueError("depth must be positive")
        self.order_book_id = order_book_id
        self.symbol = symbol
        self.depth = depth
        self._orders: dict[tuple[Side, int], _Order] = {}
        self._last_sequence_number: int | None = None

    def apply(self, event: BookEvent) -> BookSnapshot:
        if event.order_book_id != self.order_book_id:
            raise ValueError("event belongs to a different order book")
        if (
            self._last_sequence_number is not None
            and event.sequence_number <= self._last_sequence_number
        ):
            raise ValueError("sequence_number must be strictly increasing")

        match event.event_type:
            case EventType.ADD:
                self._add(event)
            case EventType.MODIFY:
                self._modify(event)
            case EventType.DELETE:
                self._delete(event)
            case EventType.EXECUTE:
                self._execute(event)

        self._last_sequence_number = event.sequence_number
        return self.snapshot(event)

    def flush(self, event: BookFlush) -> BookSnapshot:
        if event.order_book_id != self.order_book_id:
            raise ValueError("flush belongs to a different order book")
        self._validate_sequence(event.sequence_number)
        self._orders.clear()
        self._last_sequence_number = event.sequence_number
        return BookSnapshot(
            timestamp=event.timestamp,
            timestamp_ns=event.timestamp_ns,
            sequence_number=event.sequence_number,
            order_book_id=self.order_book_id,
            symbol=self.symbol,
            levels=(),
        )

    def _validate_sequence(self, sequence_number: int) -> None:
        if self._last_sequence_number is not None and sequence_number <= self._last_sequence_number:
            raise ValueError("sequence_number must be strictly increasing")

    def _add(self, event: BookEvent) -> None:
        assert event.side is not None
        key = (event.side, event.order_id)
        if key in self._orders:
            raise ValueError(f"order already exists: {event.order_id}")
        assert event.side is not None and event.price is not None and event.quantity is not None
        if event.quantity <= 0:
            raise ValueError("order quantity must be positive")
        self._orders[key] = _Order(event.side, event.price, event.quantity)

    def _modify(self, event: BookEvent) -> None:
        assert event.side is not None
        key = (event.side, event.order_id)
        order = self._require_order(key)
        assert event.quantity is not None
        if event.quantity <= 0:
            self._orders.pop(key)
            return
        order.quantity = event.quantity
        if event.price is not None:
            order.price = event.price
        if event.side is not None:
            order.side = event.side

    def _delete(self, event: BookEvent) -> None:
        assert event.side is not None
        key = (event.side, event.order_id)
        self._require_order(key)
        self._orders.pop(key)

    def _execute(self, event: BookEvent) -> None:
        assert event.side is not None
        key = (event.side, event.order_id)
        order = self._require_order(key)
        assert event.quantity is not None
        if event.quantity > order.quantity:
            raise ValueError("executed quantity cannot exceed remaining quantity")
        order.quantity -= event.quantity
        if order.quantity == 0:
            self._orders.pop(key)

    def _require_order(self, key: tuple[Side, int]) -> _Order:
        try:
            return self._orders[key]
        except KeyError as error:
            raise ValueError(f"order not found: {key[1]} ({key[0]})") from error

    def snapshot(self, event: BookEvent) -> BookSnapshot:
        aggregates: dict[Side, dict[Decimal, list[int]]] = {
            Side.BUY: defaultdict(lambda: [0, 0]),
            Side.SELL: defaultdict(lambda: [0, 0]),
        }
        for order in self._orders.values():
            aggregate = aggregates[order.side][order.price]
            aggregate[0] += order.quantity
            aggregate[1] += 1

        levels: list[PriceLevel] = []
        for side, reverse in ((Side.BUY, True), (Side.SELL, False)):
            prices = sorted(aggregates[side], reverse=reverse)[: self.depth]
            levels.extend(
                PriceLevel(
                    level=index,
                    side=side,
                    price=price,
                    quantity=aggregates[side][price][0],
                    order_count=aggregates[side][price][1],
                )
                for index, price in enumerate(prices, start=1)
            )

        return BookSnapshot(
            timestamp=event.timestamp,
            timestamp_ns=event.timestamp_ns,
            sequence_number=event.sequence_number,
            order_book_id=self.order_book_id,
            symbol=self.symbol,
            levels=tuple(levels),
        )
