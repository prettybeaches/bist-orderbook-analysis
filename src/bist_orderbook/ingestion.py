from __future__ import annotations

import csv
import ipaddress
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bist_orderbook.book import OrderBook
from bist_orderbook.capture import PcapReader, UDPDatagram, decode_ethernet_ipv4_udp, open_capture
from bist_orderbook.domain import BookEvent, BookSnapshot
from bist_orderbook.itch import BistechItchDecoder, BookFlush
from bist_orderbook.moldudp64 import MoldUDP64Packet, decode_moldudp64
from bist_orderbook.storage import SQLiteStore


@dataclass(frozen=True, slots=True)
class SelectedInstrument:
    order_book_id: int
    symbol: str
    market: str
    price_decimals: int
    underlying_symbol: str | None = None
    expiration_date: str | None = None


@dataclass(slots=True)
class IngestionStats:
    packets_read: int = 0
    multicast_datagrams: int = 0
    mold_packets: int = 0
    itch_messages: int = 0
    selected_messages: int = 0
    snapshots_written: int = 0
    sequence_gaps: int = 0
    missing_messages: int = 0
    replayed_messages: int = 0
    malformed_payloads: int = 0
    decode_errors: int = 0
    book_errors: int = 0


@dataclass(frozen=True, slots=True)
class IngestionResult:
    stats: IngestionStats
    stopped_at_limit: bool
    stopped_at_snapshot_limit: bool


def load_selected_instruments(
    pairs_path: str | Path, catalog_path: str | Path
) -> tuple[SelectedInstrument, ...]:
    with Path(catalog_path).open(encoding="utf-8", newline="") as source:
        catalog = {row["symbol"]: row for row in csv.DictReader(source)}
    with Path(pairs_path).open(encoding="utf-8", newline="") as source:
        pairs = list(csv.DictReader(source))
    if not pairs:
        raise ValueError("pair configuration is empty")

    selected: dict[int, SelectedInstrument] = {}
    for pair in pairs:
        for role, market in (("spot", "EQUITY"), ("future", "FUTURE")):
            symbol = pair[f"{role}_symbol"]
            configured_book_id = int(pair[f"{role}_order_book_id"])
            try:
                directory = catalog[symbol]
            except KeyError as error:
                raise ValueError(f"symbol is missing from instrument catalog: {symbol}") from error
            catalog_book_id = int(directory["order_book_id"])
            if configured_book_id != catalog_book_id:
                raise ValueError(
                    f"order book ID mismatch for {symbol}: pair={configured_book_id}, "
                    f"catalog={catalog_book_id}"
                )
            selected[catalog_book_id] = SelectedInstrument(
                order_book_id=catalog_book_id,
                symbol=symbol,
                market=market,
                price_decimals=int(directory["price_decimals"]),
                underlying_symbol=(directory["underlying_symbol"] or None),
                expiration_date=(directory["expiration_date"] or None),
            )
    return tuple(sorted(selected.values(), key=lambda item: item.symbol))


class ReplayProcessor:
    """Replay selected multicast books and write their L2 snapshots in batches."""

    def __init__(
        self,
        instruments: tuple[SelectedInstrument, ...],
        store: SQLiteStore,
        *,
        depth: int = 10,
        batch_size: int = 1_000,
        snapshot_every: int = 1,
        max_snapshots: int | None = None,
    ) -> None:
        if batch_size <= 0 or snapshot_every <= 0:
            raise ValueError("batch_size and snapshot_every must be positive")
        self.instruments = {item.order_book_id: item for item in instruments}
        self.books = {
            item.order_book_id: OrderBook(item.order_book_id, item.symbol, depth=depth)
            for item in instruments
        }
        self.store = store
        self.batch_size = batch_size
        self.snapshot_every = snapshot_every
        self.max_snapshots = max_snapshots
        self.stats = IngestionStats()
        self._decoders: dict[tuple[str, int, str], BistechItchDecoder] = {}
        self._next_sequences: dict[tuple[str, int, str], int] = {}
        self._event_counts: dict[int, int] = {item.order_book_id: 0 for item in instruments}
        self._snapshots: list[BookSnapshot] = []
        self._price_decimals = {
            item.order_book_id: item.price_decimals for item in instruments
        }

        self.store.upsert_instruments(
            (
                item.order_book_id,
                item.symbol,
                item.market,
                item.underlying_symbol,
                item.expiration_date,
            )
            for item in instruments
        )

    @property
    def reached_snapshot_limit(self) -> bool:
        return (
            self.max_snapshots is not None
            and self.stats.snapshots_written + len(self._snapshots) >= self.max_snapshots
        )

    def process(self, datagram: UDPDatagram) -> None:
        if not ipaddress.ip_address(datagram.destination_ip).is_multicast:
            return
        self.stats.multicast_datagrams += 1
        try:
            packet = decode_moldudp64(datagram.payload)
        except (UnicodeDecodeError, ValueError):
            self.stats.malformed_payloads += 1
            return
        self.stats.mold_packets += 1
        self.stats.itch_messages += len(packet.messages)

        key = (datagram.destination_ip, datagram.destination_port, packet.session)
        skip = self._track_sequence(key, packet)
        decoder = self._decoders.setdefault(
            key, BistechItchDecoder(price_decimals=self._price_decimals)
        )
        for index, message in enumerate(packet.messages):
            if index < skip:
                continue
            if not message:
                self.stats.decode_errors += 1
                continue
            message_type = chr(message[0])
            if message_type == "T":
                self._decode(decoder, message, packet.sequence_number + index)
                continue
            order_book_id = self._selected_order_book_id(message_type, message)
            if order_book_id not in self.books:
                continue
            self.stats.selected_messages += 1
            decoded = self._decode(decoder, message, packet.sequence_number + index)
            if isinstance(decoded, (BookEvent, BookFlush)):
                self._apply(decoded)
            if self.reached_snapshot_limit:
                return

    def _track_sequence(
        self, key: tuple[str, int, str], packet: MoldUDP64Packet
    ) -> int:
        message_count = len(packet.messages)
        if message_count == 0:
            return 0
        expected = self._next_sequences.get(key)
        skip = 0
        if expected is not None:
            if packet.sequence_number > expected:
                self.stats.sequence_gaps += 1
                self.stats.missing_messages += packet.sequence_number - expected
            elif packet.sequence_number < expected:
                skip = min(expected - packet.sequence_number, message_count)
                self.stats.replayed_messages += skip
        self._next_sequences[key] = max(
            self._next_sequences.get(key, 0), packet.sequence_number + message_count
        )
        return skip

    def _decode(
        self, decoder: BistechItchDecoder, message: bytes, sequence_number: int
    ) -> object | None:
        try:
            return decoder.decode_message(message, sequence_number)
        except ValueError:
            self.stats.decode_errors += 1
            return None

    @staticmethod
    def _selected_order_book_id(message_type: str, message: bytes) -> int | None:
        if message_type in ("A", "E", "C", "D", "U") and len(message) >= 17:
            return int.from_bytes(message[13:17], "big")
        if message_type in ("R", "O", "V", "Y", "Z") and len(message) >= 9:
            return int.from_bytes(message[5:9], "big")
        return None

    def _apply(self, event: BookEvent | BookFlush) -> None:
        book = self.books[event.order_book_id]
        try:
            snapshot = book.apply(event) if isinstance(event, BookEvent) else book.flush(event)
        except ValueError:
            self.stats.book_errors += 1
            return
        self._event_counts[event.order_book_id] += 1
        if (
            isinstance(event, BookFlush)
            or self._event_counts[event.order_book_id] % self.snapshot_every == 0
        ):
            self._snapshots.append(snapshot)
        if len(self._snapshots) >= self.batch_size or self.reached_snapshot_limit:
            self.flush()

    def flush(self) -> None:
        if self.max_snapshots is not None:
            remaining = self.max_snapshots - self.stats.snapshots_written
            snapshots = self._snapshots[:remaining]
        else:
            snapshots = self._snapshots
        self.stats.snapshots_written += self.store.write_snapshots(snapshots)
        self._snapshots.clear()


def ingest_capture(
    capture: str | Path,
    pairs: str | Path,
    catalog: str | Path,
    database: str | Path,
    *,
    limit: int | None = None,
    max_snapshots: int | None = None,
    batch_size: int = 1_000,
    snapshot_every: int = 1,
    progress_every: int = 1_000_000,
    progress: Callable[[IngestionStats], None] | None = None,
) -> IngestionResult:
    instruments = load_selected_instruments(pairs, catalog)
    processor = ReplayProcessor(
        instruments,
        SQLiteStore(database),
        batch_size=batch_size,
        snapshot_every=snapshot_every,
        max_snapshots=max_snapshots,
    )
    stopped_at_limit = False
    with open_capture(capture) as (_, stream):
        reader = PcapReader(stream)
        if reader.link_type != 1:
            raise ValueError(
                f"unsupported PCAP link type: {reader.link_type}; expected Ethernet (1)"
            )
        for record in reader:
            processor.stats.packets_read += 1
            datagram = decode_ethernet_ipv4_udp(record.data)
            if datagram is not None:
                processor.process(datagram)
            if progress and progress_every and processor.stats.packets_read % progress_every == 0:
                progress(processor.stats)
            if processor.reached_snapshot_limit:
                break
            if limit is not None and processor.stats.packets_read >= limit:
                stopped_at_limit = True
                break
    processor.flush()
    return IngestionResult(
        stats=processor.stats,
        stopped_at_limit=stopped_at_limit,
        stopped_at_snapshot_limit=processor.reached_snapshot_limit,
    )
