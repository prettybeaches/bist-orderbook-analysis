from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from bist_orderbook.domain import BookEvent, EventType, Side
from bist_orderbook.moldudp64 import MoldUDP64Packet


@dataclass(frozen=True, slots=True)
class InstrumentDirectory:
    timestamp: datetime
    sequence_number: int
    order_book_id: int
    symbol: str
    long_name: str
    isin: str
    financial_product: int
    currency: str
    price_decimals: int
    nominal_decimals: int
    underlying_order_book_id: int
    expiration_date: int
    ranking_type: int


@dataclass(frozen=True, slots=True)
class BookFlush:
    timestamp: datetime
    sequence_number: int
    order_book_id: int
    timestamp_ns: int | None = None


DecodedMessage = InstrumentDirectory | BookEvent | BookFlush


class BistechItchDecoder:
    """Stateful BISTECH ITCH 2112 decoder for messages needed to build order books."""

    def __init__(self, price_decimals: dict[int, int] | None = None) -> None:
        self._seconds: int | None = None
        self._price_decimals: dict[int, int] = dict(price_decimals or {})

    def decode_packet(self, packet: MoldUDP64Packet) -> list[DecodedMessage]:
        decoded: list[DecodedMessage] = []
        for index, message in enumerate(packet.messages):
            result = self.decode_message(message, packet.sequence_number + index)
            if result is not None:
                decoded.append(result)
        return decoded

    def decode_message(self, message: bytes, sequence_number: int) -> DecodedMessage | None:
        if not message:
            raise ValueError("empty ITCH message")
        message_type = chr(message[0])
        if message_type == "T":
            self._require_length(message, 5)
            self._seconds = self._unsigned(message, 1, 4)
            return None
        if message_type == "R":
            self._require_length(message, 130)
            directory = InstrumentDirectory(
                timestamp=self._timestamp(message),
                sequence_number=sequence_number,
                order_book_id=self._unsigned(message, 5, 4),
                symbol=self._alpha(message, 9, 32),
                long_name=self._alpha(message, 41, 32),
                isin=self._alpha(message, 73, 12),
                financial_product=self._unsigned(message, 85, 1),
                currency=self._alpha(message, 86, 3),
                price_decimals=self._unsigned(message, 89, 2),
                nominal_decimals=self._unsigned(message, 91, 2),
                underlying_order_book_id=self._unsigned(message, 114, 4),
                expiration_date=self._unsigned(message, 122, 4),
                ranking_type=self._unsigned(message, 129, 1),
            )
            self._price_decimals[directory.order_book_id] = directory.price_decimals
            return directory
        if message_type == "A":
            self._require_length(message, 45)
            book_id = self._unsigned(message, 13, 4)
            return BookEvent(
                timestamp=self._timestamp(message),
                sequence_number=sequence_number,
                order_book_id=book_id,
                event_type=EventType.ADD,
                order_id=self._unsigned(message, 5, 8),
                side=self._side(message[17]),
                quantity=self._unsigned(message, 22, 8),
                price=self._price(message, 30, book_id),
                timestamp_ns=self._timestamp_ns(message),
            )
        if message_type in ("E", "C"):
            self._require_length(message, 52 if message_type == "E" else 58)
            return BookEvent(
                timestamp=self._timestamp(message),
                sequence_number=sequence_number,
                order_book_id=self._unsigned(message, 13, 4),
                event_type=EventType.EXECUTE,
                order_id=self._unsigned(message, 5, 8),
                side=self._side(message[17]),
                quantity=self._unsigned(message, 18, 8),
                timestamp_ns=self._timestamp_ns(message),
            )
        if message_type == "D":
            self._require_length(message, 18)
            return BookEvent(
                timestamp=self._timestamp(message),
                sequence_number=sequence_number,
                order_book_id=self._unsigned(message, 13, 4),
                event_type=EventType.DELETE,
                order_id=self._unsigned(message, 5, 8),
                side=self._side(message[17]),
                timestamp_ns=self._timestamp_ns(message),
            )
        if message_type == "Y":
            self._require_length(message, 9)
            return BookFlush(
                timestamp=self._timestamp(message),
                sequence_number=sequence_number,
                order_book_id=self._unsigned(message, 5, 4),
                timestamp_ns=self._timestamp_ns(message),
            )
        return None

    def _timestamp(self, message: bytes) -> datetime:
        timestamp_ns = self._timestamp_ns(message)
        seconds, nanoseconds = divmod(timestamp_ns, 1_000_000_000)
        return datetime.fromtimestamp(seconds, UTC).replace(microsecond=nanoseconds // 1_000)

    def _timestamp_ns(self, message: bytes) -> int:
        if self._seconds is None:
            raise ValueError("timestamped message received before an ITCH T message")
        nanoseconds = self._unsigned(message, 1, 4)
        return self._seconds * 1_000_000_000 + nanoseconds

    def _price(self, message: bytes, offset: int, order_book_id: int) -> Decimal:
        try:
            decimals = self._price_decimals[order_book_id]
        except KeyError as error:
            raise ValueError(f"order book directory not found: {order_book_id}") from error
        raw_price = int.from_bytes(message[offset : offset + 4], "big", signed=True)
        return Decimal(raw_price).scaleb(-decimals)

    @staticmethod
    def _unsigned(message: bytes, offset: int, length: int) -> int:
        return int.from_bytes(message[offset : offset + length], "big")

    @staticmethod
    def _alpha(message: bytes, offset: int, length: int) -> str:
        return message[offset : offset + length].decode("latin-1").rstrip(" \x00")

    @staticmethod
    def _side(value: int) -> Side:
        try:
            return Side(chr(value))
        except ValueError as error:
            raise ValueError(f"invalid order side: {value:#x}") from error

    @staticmethod
    def _require_length(message: bytes, expected: int) -> None:
        if len(message) != expected:
            raise ValueError(
                f"{chr(message[0])} message is {len(message)} bytes; expected {expected}"
            )
