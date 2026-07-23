import io
import struct
import unittest

from bist_orderbook.capture import PcapReader, decode_ethernet_ipv4_udp


class CaptureTest(unittest.TestCase):
    def test_reads_nanosecond_little_endian_pcap(self) -> None:
        global_header = struct.pack("<IHHIIII", 0xA1B23C4D, 2, 4, 0, 0, 16_384, 1)
        packet = b"abc"
        record_header = struct.pack("<IIII", 10, 123, len(packet), len(packet))
        reader = PcapReader(io.BytesIO(global_header + record_header + packet))

        record = next(iter(reader))

        self.assertEqual(record.timestamp_ns, 10_000_000_123)
        self.assertEqual(record.data, packet)
        self.assertEqual(reader.link_type, 1)

    def test_decodes_ethernet_ipv4_udp(self) -> None:
        ethernet = bytes.fromhex("01005e71d849a0b439f708010800")
        ip = bytes.fromhex("4500002000000000301100000ac2c80fe971d849")
        udp = struct.pack("!HHHH", 37150, 21001, 12, 0)
        datagram = decode_ethernet_ipv4_udp(ethernet + ip + udp + b"ITCH")

        self.assertIsNotNone(datagram)
        assert datagram is not None
        self.assertEqual(datagram.destination_ip, "233.113.216.73")
        self.assertEqual(datagram.destination_port, 21001)
        self.assertEqual(datagram.payload, b"ITCH")


if __name__ == "__main__":
    unittest.main()

