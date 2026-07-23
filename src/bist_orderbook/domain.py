from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class Side(StrEnum):
    BUY = "B"
    SELL = "S"


class EventType(StrEnum):
    ADD = "add"
    MODIFY = "modify"
    DELETE = "delete"
    EXECUTE = "execute"


@dataclass(frozen=True, slots=True)
class BookEvent:
    """Protocol-independent event emitted by a market-data decoder."""

    timestamp: datetime
    sequence_number: int
    order_book_id: int
    event_type: EventType
    order_id: int
    side: Side | None = None
    price: Decimal | None = None
    quantity: int | None = None
    timestamp_ns: int | None = None

    def __post_init__(self) -> None:
        if self.sequence_number < 0:
            raise ValueError("sequence_number cannot be negative")
        if self.order_book_id < 0 or self.order_id < 0:
            raise ValueError("identifiers cannot be negative")
        if self.quantity is not None and self.quantity < 0:
            raise ValueError("quantity cannot be negative")
        if self.event_type == EventType.ADD:
            if self.side is None or self.price is None or self.quantity is None:
                raise ValueError("add event requires side, price, and quantity")
        if self.event_type == EventType.MODIFY and self.quantity is None:
            raise ValueError("modify event requires quantity")
        if self.event_type in (EventType.MODIFY, EventType.DELETE, EventType.EXECUTE):
            if self.side is None:
                raise ValueError(f"{self.event_type.value} event requires side")
        if self.event_type == EventType.EXECUTE:
            if self.quantity is None or self.quantity <= 0:
                raise ValueError("execute event requires a positive quantity")


@dataclass(frozen=True, slots=True)
class PriceLevel:
    level: int
    side: Side
    price: Decimal
    quantity: int
    order_count: int


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    timestamp: datetime
    sequence_number: int
    order_book_id: int
    symbol: str
    levels: tuple[PriceLevel, ...]
    timestamp_ns: int | None = None
