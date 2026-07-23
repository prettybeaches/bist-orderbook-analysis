from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bist_orderbook.capture import PcapReader, UDPDatagram, decode_ethernet_ipv4_udp, open_capture
from bist_orderbook.itch import BistechItchDecoder, InstrumentDirectory
from bist_orderbook.moldudp64 import decode_moldudp64


ChannelKey = tuple[str, int, str, int, str]


@dataclass(slots=True)
class DiscoveryStats:
    packets_read: int = 0
    udp_datagrams: int = 0
    mold_packets: int = 0
    itch_messages: int = 0
    malformed_payloads: int = 0
    sequence_gaps: int = 0
    missing_messages: int = 0
    duplicate_or_replayed_packets: int = 0


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    instruments: tuple[InstrumentDirectory, ...]
    stats: DiscoveryStats


class InstrumentDiscovery:
    """Collect instrument directories while preserving independent channel state."""

    def __init__(self) -> None:
        self.instruments: dict[int, InstrumentDirectory] = {}
        self.stats = DiscoveryStats()
        self._decoders: dict[ChannelKey, BistechItchDecoder] = {}
        self._next_sequences: dict[ChannelKey, int] = {}

    def process(self, datagram: UDPDatagram) -> None:
        self.stats.udp_datagrams += 1
        try:
            packet = decode_moldudp64(datagram.payload)
        except (UnicodeDecodeError, ValueError):
            self.stats.malformed_payloads += 1
            return

        self.stats.mold_packets += 1
        key = (
            datagram.source_ip,
            datagram.source_port,
            datagram.destination_ip,
            datagram.destination_port,
            packet.session,
        )
        self._track_sequence(key, packet.sequence_number, len(packet.messages))
        decoder = self._decoders.setdefault(key, BistechItchDecoder())
        self.stats.itch_messages += len(packet.messages)

        for index, message in enumerate(packet.messages):
            if not message or message[0] not in (ord("T"), ord("R")):
                continue
            try:
                decoded = decoder.decode_message(message, packet.sequence_number + index)
            except ValueError:
                self.stats.malformed_payloads += 1
                continue
            if isinstance(decoded, InstrumentDirectory):
                self.instruments[decoded.order_book_id] = decoded

    def _track_sequence(self, key: ChannelKey, sequence: int, message_count: int) -> None:
        if message_count == 0:
            return
        expected = self._next_sequences.get(key)
        if expected is not None:
            if sequence > expected:
                self.stats.sequence_gaps += 1
                self.stats.missing_messages += sequence - expected
            elif sequence < expected:
                self.stats.duplicate_or_replayed_packets += 1
        self._next_sequences[key] = max(self._next_sequences.get(key, 0), sequence + message_count)


def discover_instruments(
    capture: str | Path,
    *,
    limit: int | None = None,
    progress_every: int = 1_000_000,
    progress: Callable[[DiscoveryStats, int], None] | None = None,
) -> DiscoveryResult:
    discovery = InstrumentDiscovery()
    with open_capture(capture) as (_, stream):
        reader = PcapReader(stream)
        if reader.link_type != 1:
            raise ValueError(
                f"unsupported PCAP link type: {reader.link_type}; expected Ethernet (1)"
            )
        for record in reader:
            discovery.stats.packets_read += 1
            datagram = decode_ethernet_ipv4_udp(record.data)
            if datagram is not None:
                discovery.process(datagram)
            if progress and progress_every and discovery.stats.packets_read % progress_every == 0:
                progress(discovery.stats, len(discovery.instruments))
            if limit is not None and discovery.stats.packets_read >= limit:
                break

    instruments = tuple(
        sorted(discovery.instruments.values(), key=lambda item: (item.symbol, item.order_book_id))
    )
    return DiscoveryResult(instruments=instruments, stats=discovery.stats)


def market_name(financial_product: int) -> str:
    return {
        1: "OPTION",
        2: "FORWARD",
        3: "FUTURE",
        5: "CASH",
        14: "EQUITY_WARRANT",
        18: "CERTIFICATE",
    }.get(financial_product, f"PRODUCT_{financial_product}")


def write_instruments_csv(path: str | Path, instruments: tuple[InstrumentDirectory, ...]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    symbols_by_book = {item.order_book_id: item.symbol for item in instruments}
    # BIST cash instruments expose a legacy underlying identifier in the same
    # directory field referenced by their derivative contracts.
    symbols_by_underlying = {
        item.underlying_order_book_id: item.symbol
        for item in instruments
        if item.financial_product == 5 and item.underlying_order_book_id
    }
    fieldnames = [
        "order_book_id",
        "symbol",
        "long_name",
        "isin",
        "market",
        "financial_product",
        "currency",
        "price_decimals",
        "nominal_decimals",
        "underlying_order_book_id",
        "underlying_symbol",
        "expiration_date",
        "ranking_type",
        "sequence_number",
        "timestamp",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for item in instruments:
            writer.writerow(
                {
                    "order_book_id": item.order_book_id,
                    "symbol": item.symbol,
                    "long_name": item.long_name,
                    "isin": item.isin,
                    "market": market_name(item.financial_product),
                    "financial_product": item.financial_product,
                    "currency": item.currency,
                    "price_decimals": item.price_decimals,
                    "nominal_decimals": item.nominal_decimals,
                    "underlying_order_book_id": item.underlying_order_book_id or "",
                    "underlying_symbol": symbols_by_book.get(
                        item.underlying_order_book_id,
                        symbols_by_underlying.get(item.underlying_order_book_id, ""),
                    ),
                    "expiration_date": item.expiration_date or "",
                    "ranking_type": item.ranking_type,
                    "sequence_number": item.sequence_number,
                    "timestamp": item.timestamp.isoformat(),
                }
            )
