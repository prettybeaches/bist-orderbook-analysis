from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol

from bist_orderbook.domain import BookEvent


@dataclass(frozen=True, slots=True)
class PacketPayload:
    """UDP/TCP payload plus capture metadata, independent of the PCAP library."""

    captured_at: datetime
    payload: bytes
    source: str | None = None
    destination: str | None = None


class MarketDataDecoder(Protocol):
    """Contract to implement after the exact BIST feed specification is known."""

    def decode(self, packet: PacketPayload) -> Iterable[BookEvent]: ...


class UnsupportedFeedDecoder:
    def decode(self, packet: PacketPayload) -> Iterable[BookEvent]:
        raise NotImplementedError(
            "the BIST feed protocol and message schema used by the PCAP are required"
        )
