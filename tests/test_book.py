import unittest
from datetime import UTC, datetime
from decimal import Decimal

from bist_orderbook.book import OrderBook
from bist_orderbook.domain import BookEvent, EventType, Side


class OrderBookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.book = OrderBook(order_book_id=42, symbol="ASELS", depth=10)
        self.timestamp = datetime(2026, 4, 27, 7, 0, tzinfo=UTC)

    def event(
        self,
        sequence: int,
        event_type: EventType,
        order_id: int,
        side: Side | None = None,
        price: str | None = None,
        quantity: int | None = None,
    ) -> BookEvent:
        return BookEvent(
            timestamp=self.timestamp,
            sequence_number=sequence,
            order_book_id=42,
            event_type=event_type,
            order_id=order_id,
            side=side,
            price=Decimal(price) if price is not None else None,
            quantity=quantity,
        )

    def test_aggregates_orders_and_sorts_both_sides(self) -> None:
        self.book.apply(self.event(1, EventType.ADD, 1, Side.BUY, "10.00", 100))
        self.book.apply(self.event(2, EventType.ADD, 2, Side.BUY, "10.00", 50))
        self.book.apply(self.event(3, EventType.ADD, 3, Side.BUY, "9.90", 200))
        snapshot = self.book.apply(
            self.event(4, EventType.ADD, 4, Side.SELL, "10.10", 80)
        )

        buy_levels = [level for level in snapshot.levels if level.side == Side.BUY]
        sell_levels = [level for level in snapshot.levels if level.side == Side.SELL]
        self.assertEqual((buy_levels[0].price, buy_levels[0].quantity), (Decimal("10"), 150))
        self.assertEqual(buy_levels[0].order_count, 2)
        self.assertEqual(sell_levels[0].price, Decimal("10.10"))

    def test_execute_reduces_and_removes_order(self) -> None:
        self.book.apply(self.event(1, EventType.ADD, 1, Side.BUY, "10.00", 100))
        snapshot = self.book.apply(
            self.event(2, EventType.EXECUTE, 1, side=Side.BUY, quantity=100)
        )
        self.assertEqual(snapshot.levels, ())

    def test_rejects_non_increasing_sequence(self) -> None:
        self.book.apply(self.event(2, EventType.ADD, 1, Side.BUY, "10.00", 100))
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            self.book.apply(self.event(2, EventType.DELETE, 1, side=Side.BUY))

    def test_order_id_is_unique_per_book_and_side(self) -> None:
        self.book.apply(self.event(1, EventType.ADD, 1, Side.BUY, "10.00", 100))
        snapshot = self.book.apply(self.event(2, EventType.ADD, 1, Side.SELL, "10.10", 50))
        self.assertEqual(len(snapshot.levels), 2)


if __name__ == "__main__":
    unittest.main()
