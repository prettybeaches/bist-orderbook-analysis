import unittest
from datetime import UTC, datetime
from decimal import Decimal

from bist_orderbook.domain import BookEvent, EventType, Side


class BookEventTest(unittest.TestCase):
    def test_add_event_requires_book_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "side, price, and quantity"):
            BookEvent(
                timestamp=datetime.now(UTC),
                sequence_number=1,
                order_book_id=42,
                event_type=EventType.ADD,
                order_id=99,
            )

    def test_valid_add_event(self) -> None:
        event = BookEvent(
            timestamp=datetime.now(UTC),
            sequence_number=1,
            order_book_id=42,
            event_type=EventType.ADD,
            order_id=99,
            side=Side.BUY,
            price=Decimal("53.25"),
            quantity=100,
        )
        self.assertEqual(event.price, Decimal("53.25"))


if __name__ == "__main__":
    unittest.main()
