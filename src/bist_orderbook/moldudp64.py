from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MoldUDP64Packet:
    session: str
    sequence_number: int
    messages: tuple[bytes, ...]

    @property
    def is_heartbeat(self) -> bool:
        return not self.messages


def decode_moldudp64(payload: bytes) -> MoldUDP64Packet:
    """Decode a MoldUDP64 downstream packet and validate all message framing."""

    if len(payload) < 20:
        raise ValueError("MoldUDP64 packet is shorter than 20 bytes")
    session_bytes = payload[:10]
    sequence_number = int.from_bytes(payload[10:18], "big")
    message_count = int.from_bytes(payload[18:20], "big")
    offset = 20
    messages: list[bytes] = []
    for _ in range(message_count):
        if offset + 2 > len(payload):
            raise ValueError("truncated MoldUDP64 message length")
        message_length = int.from_bytes(payload[offset : offset + 2], "big")
        offset += 2
        message_end = offset + message_length
        if message_end > len(payload):
            raise ValueError("truncated MoldUDP64 message body")
        messages.append(payload[offset:message_end])
        offset = message_end
    if offset != len(payload):
        raise ValueError("unexpected trailing data in MoldUDP64 packet")
    return MoldUDP64Packet(
        session=session_bytes.rstrip(b"\x00 ").decode("ascii"),
        sequence_number=sequence_number,
        messages=tuple(messages),
    )
