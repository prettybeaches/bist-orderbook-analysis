from __future__ import annotations

import ipaddress
import struct
import tarfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator


_PCAP_MAGIC = {
    b"\xd4\xc3\xb2\xa1": ("<", 1_000),
    b"\xa1\xb2\xc3\xd4": (">", 1_000),
    b"\x4d\x3c\xb2\xa1": ("<", 1),
    b"\xa1\xb2\x3c\x4d": (">", 1),
}


@dataclass(frozen=True, slots=True)
class CaptureRecord:
    timestamp_ns: int
    captured_length: int
    original_length: int
    data: bytes


@dataclass(frozen=True, slots=True)
class UDPDatagram:
    source_ip: str
    destination_ip: str
    source_port: int
    destination_port: int
    payload: bytes


class PcapReader:
    """Streaming classic-PCAP reader supporting micro- and nanosecond captures."""

    def __init__(self, stream: BinaryIO) -> None:
        header = stream.read(24)
        if len(header) != 24:
            raise ValueError("truncated PCAP global header")
        try:
            self.byte_order, self.fraction_to_ns = _PCAP_MAGIC[header[:4]]
        except KeyError as error:
            raise ValueError(f"unsupported PCAP magic: {header[:4].hex()}") from error
        _, _, _, _, _, self.snaplen, self.link_type = struct.unpack(
            f"{self.byte_order}IHHIIII", header
        )
        self.stream = stream

    def __iter__(self) -> Iterator[CaptureRecord]:
        record_struct = struct.Struct(f"{self.byte_order}IIII")
        while header := self.stream.read(record_struct.size):
            if len(header) != record_struct.size:
                raise ValueError("truncated PCAP packet header")
            seconds, fraction, captured_length, original_length = record_struct.unpack(header)
            data = self.stream.read(captured_length)
            if len(data) != captured_length:
                raise ValueError("truncated PCAP packet data")
            yield CaptureRecord(
                timestamp_ns=seconds * 1_000_000_000 + fraction * self.fraction_to_ns,
                captured_length=captured_length,
                original_length=original_length,
                data=data,
            )


@contextmanager
def open_capture(path: str | Path) -> Iterator[tuple[str, BinaryIO]]:
    """Open a plain PCAP or the first PCAP member of a tar.xz without extraction."""

    capture_path = Path(path)
    if capture_path.name.endswith(".tar.xz"):
        with tarfile.open(capture_path, mode="r|xz") as archive:
            for member in archive:
                if member.isfile() and member.name.lower().endswith((".pcap", ".cap")):
                    stream = archive.extractfile(member)
                    if stream is None:
                        break
                    yield member.name, stream
                    return
        raise ValueError("no PCAP file found in archive")

    with capture_path.open("rb") as stream:
        yield capture_path.name, stream


def decode_ethernet_ipv4_udp(frame: bytes) -> UDPDatagram | None:
    if len(frame) < 14:
        return None
    offset = 14
    ether_type = int.from_bytes(frame[12:14], "big")
    while ether_type in (0x8100, 0x88A8):
        if len(frame) < offset + 4:
            return None
        ether_type = int.from_bytes(frame[offset + 2 : offset + 4], "big")
        offset += 4
    if ether_type != 0x0800 or len(frame) < offset + 20:
        return None

    version_and_ihl = frame[offset]
    if version_and_ihl >> 4 != 4:
        return None
    ip_header_length = (version_and_ihl & 0x0F) * 4
    if ip_header_length < 20 or len(frame) < offset + ip_header_length + 8:
        return None
    if frame[offset + 9] != 17:
        return None

    source_ip = str(ipaddress.ip_address(frame[offset + 12 : offset + 16]))
    destination_ip = str(ipaddress.ip_address(frame[offset + 16 : offset + 20]))
    udp_offset = offset + ip_header_length
    source_port, destination_port, udp_length, _ = struct.unpack(
        "!HHHH", frame[udp_offset : udp_offset + 8]
    )
    if udp_length < 8 or len(frame) < udp_offset + udp_length:
        return None
    return UDPDatagram(
        source_ip=source_ip,
        destination_ip=destination_ip,
        source_port=source_port,
        destination_port=destination_port,
        payload=frame[udp_offset + 8 : udp_offset + udp_length],
    )
