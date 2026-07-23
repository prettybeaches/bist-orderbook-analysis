from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from bist_orderbook.analysis import (
    analyze_pair,
    load_symbol_pairs,
    write_analysis_summary,
    write_pair_reports,
)
from bist_orderbook.capture import PcapReader, decode_ethernet_ipv4_udp, open_capture
from bist_orderbook.discovery import discover_instruments, market_name, write_instruments_csv
from bist_orderbook.ingestion import IngestionStats, ingest_capture
from bist_orderbook.moldudp64 import decode_moldudp64
from bist_orderbook.query import (
    SnapshotQuery,
    format_snapshots,
    parse_time_ns,
    query_snapshots,
    write_snapshot_csv,
)
from bist_orderbook.storage import SQLiteStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bist-orderbook",
        description="BIST PCAP order book reconstruction and analysis tools",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db = subparsers.add_parser("init-db", help="Create the SQLite schema")
    init_db.add_argument(
        "--database",
        type=Path,
        default=Path("data/processed/orderbook.db"),
        help="Path to the SQLite database",
    )

    inspect_pcap = subparsers.add_parser(
        "inspect-pcap", help="Summarize packets without extracting the PCAP"
    )
    inspect_pcap.add_argument("capture", type=Path, help="Path to a PCAP or .tar.xz archive")
    inspect_pcap.add_argument("--limit", type=int, default=1_000, help="Number of packets to read")
    inspect_pcap.add_argument(
        "--samples", type=int, default=10, help="Number of payload samples to print"
    )
    inspect_pcap.add_argument(
        "--sample-min-length",
        type=int,
        default=0,
        help="Minimum UDP payload length for printed samples",
    )
    inspect_pcap.add_argument(
        "--stop-after-samples",
        action="store_true",
        help="Stop scanning after collecting the requested number of samples",
    )

    list_instruments = subparsers.add_parser(
        "list-instruments", help="Discover instruments and export their directory data"
    )
    list_instruments.add_argument("capture", type=Path, help="Path to a PCAP or .tar.xz archive")
    list_instruments.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/instruments.csv"),
        help="Destination CSV path",
    )
    list_instruments.add_argument(
        "--limit", type=int, help="Optional maximum number of capture packets to scan"
    )
    list_instruments.add_argument(
        "--progress-every",
        type=int,
        default=1_000_000,
        help="Print progress after this many packets; use 0 to disable",
    )

    ingest = subparsers.add_parser(
        "ingest", help="Replay selected order books and persist 10-level snapshots"
    )
    ingest.add_argument("capture", type=Path, help="Path to a PCAP or .tar.xz archive")
    ingest.add_argument(
        "--pairs", type=Path, default=Path("config/symbol_pairs.csv"), help="Pair CSV path"
    )
    ingest.add_argument(
        "--catalog",
        type=Path,
        default=Path("data/processed/instruments.csv"),
        help="Instrument catalog CSV path",
    )
    ingest.add_argument(
        "--database",
        type=Path,
        default=Path("data/processed/orderbook.db"),
        help="Destination SQLite database",
    )
    ingest.add_argument("--limit", type=int, help="Optional capture-packet scan limit")
    ingest.add_argument(
        "--max-snapshots", type=int, help="Stop after writing this many snapshots"
    )
    ingest.add_argument(
        "--snapshot-every",
        type=int,
        default=1,
        help="Persist every Nth selected order-book event",
    )
    ingest.add_argument("--batch-size", type=int, default=1_000)
    ingest.add_argument("--progress-every", type=int, default=1_000_000)

    query = subparsers.add_parser(
        "query", help="Query stored 10-level order book snapshots"
    )
    query.add_argument(
        "--database",
        type=Path,
        default=Path("data/processed/orderbook.db"),
        help="Source SQLite database",
    )
    query.add_argument("--symbol", help="Exact instrument symbol")
    query.add_argument("--order-book-id", type=int)
    query.add_argument("--sequence", type=int, help="Exact MoldUDP64 sequence number")
    query.add_argument(
        "--start", help="Inclusive ISO-8601 time with offset, or Unix nanoseconds"
    )
    query.add_argument(
        "--end", help="Inclusive ISO-8601 time with offset, or Unix nanoseconds"
    )
    query.add_argument("--limit", type=int, default=20, help="Maximum snapshot count")
    query.add_argument("--latest", action="store_true", help="Return newest snapshots first")
    query.add_argument("--output", type=Path, help="Optional flattened CSV output path")

    analyze = subparsers.add_parser(
        "analyze", help="Generate spot/futures CSV reports and SVG charts"
    )
    analyze.add_argument(
        "--database",
        type=Path,
        default=Path("data/processed/orderbook.db"),
        help="Source SQLite database",
    )
    analyze.add_argument(
        "--pairs", type=Path, default=Path("config/symbol_pairs.csv"), help="Pair CSV path"
    )
    analyze.add_argument(
        "--output", type=Path, default=Path("reports/analysis"), help="Report directory"
    )
    analyze.add_argument("--interval-ms", type=int, default=1_000)
    analyze.add_argument("--max-staleness-ms", type=int, default=5_000)
    analyze.add_argument("--momentum-periods", type=int, default=5)
    analyze.add_argument("--max-lag-steps", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init-db":
        store = SQLiteStore(args.database)
        store.initialize()
        print(f"Database ready: {args.database}")
        return 0
    if args.command == "inspect-pcap":
        if args.limit <= 0 or args.samples < 0:
            raise SystemExit("--limit must be positive and --samples cannot be negative")
        endpoints: Counter[tuple[str, int, str, int]] = Counter()
        payload_lengths: Counter[int] = Counter()
        sample_count = 0
        packet_count = 0
        with open_capture(args.capture) as (member_name, stream):
            reader = PcapReader(stream)
            print(
                f"Source: {member_name}; link_type={reader.link_type}; snaplen={reader.snaplen}"
            )
            for record in reader:
                packet_count += 1
                datagram = decode_ethernet_ipv4_udp(record.data)
                if datagram is not None:
                    endpoints[
                        (
                            datagram.source_ip,
                            datagram.source_port,
                            datagram.destination_ip,
                            datagram.destination_port,
                        )
                    ] += 1
                    payload_lengths[len(datagram.payload)] += 1
                    if (
                        sample_count < args.samples
                        and len(datagram.payload) >= args.sample_min_length
                    ):
                        printable = "".join(
                            chr(byte) if 32 <= byte < 127 else "." for byte in datagram.payload
                        )
                        mold_summary = ""
                        try:
                            mold_packet = decode_moldudp64(datagram.payload)
                            message_types = [
                                chr(message[0]) if message and 32 <= message[0] < 127 else "?"
                                for message in mold_packet.messages
                            ]
                            mold_summary = (
                                f" mold_session={mold_packet.session}"
                                f" mold_seq={mold_packet.sequence_number}"
                                f" messages={len(mold_packet.messages)}"
                                f" types={message_types}"
                            )
                        except (UnicodeDecodeError, ValueError):
                            pass
                        print(
                            f"sample[{sample_count + 1}] ts_ns={record.timestamp_ns} "
                            f"udp={datagram.source_ip}:{datagram.source_port}->"
                            f"{datagram.destination_ip}:{datagram.destination_port} "
                            f"len={len(datagram.payload)} hex={datagram.payload.hex()} "
                            f"ascii={printable}{mold_summary}"
                        )
                        sample_count += 1
                        if args.stop_after_samples and sample_count >= args.samples:
                            break
                if packet_count >= args.limit:
                    break
        print(f"Packets read: {packet_count}")
        print(f"UDP endpoints: {endpoints.most_common()}")
        print(f"Payload lengths: {payload_lengths.most_common()}")
        return 0
    if args.command == "list-instruments":
        if args.limit is not None and args.limit <= 0:
            raise SystemExit("--limit must be positive")
        if args.progress_every < 0:
            raise SystemExit("--progress-every cannot be negative")

        def report_progress(stats: object, instrument_count: int) -> None:
            print(
                f"Progress: packets={stats.packets_read:,} "
                f"ITCH messages={stats.itch_messages:,} instruments={instrument_count:,}",
                flush=True,
            )

        result = discover_instruments(
            args.capture,
            limit=args.limit,
            progress_every=args.progress_every,
            progress=report_progress,
        )
        write_instruments_csv(args.output, result.instruments)
        markets = Counter(market_name(item.financial_product) for item in result.instruments)
        print(f"Instrument catalog written: {args.output}")
        print(f"Instruments: {len(result.instruments):,}; markets: {dict(markets)}")
        print(
            f"Packets: {result.stats.packets_read:,}; "
            f"ITCH messages: {result.stats.itch_messages:,}; "
            f"sequence gaps: {result.stats.sequence_gaps:,}; "
            f"missing messages: {result.stats.missing_messages:,}; "
            f"replayed packets: {result.stats.duplicate_or_replayed_packets:,}; "
            f"malformed payloads: {result.stats.malformed_payloads:,}"
        )
        return 0
    if args.command == "ingest":
        positive_values = {
            "--limit": args.limit,
            "--max-snapshots": args.max_snapshots,
            "--snapshot-every": args.snapshot_every,
            "--batch-size": args.batch_size,
        }
        for option, value in positive_values.items():
            if value is not None and value <= 0:
                raise SystemExit(f"{option} must be positive")
        if args.progress_every < 0:
            raise SystemExit("--progress-every cannot be negative")

        def report_ingestion(stats: IngestionStats) -> None:
            print(
                f"Progress: packets={stats.packets_read:,} "
                f"ITCH messages={stats.itch_messages:,} "
                f"selected={stats.selected_messages:,} "
                f"snapshots={stats.snapshots_written:,}",
                flush=True,
            )

        result = ingest_capture(
            args.capture,
            args.pairs,
            args.catalog,
            args.database,
            limit=args.limit,
            max_snapshots=args.max_snapshots,
            batch_size=args.batch_size,
            snapshot_every=args.snapshot_every,
            progress_every=args.progress_every,
            progress=report_ingestion,
        )
        stats = result.stats
        print(f"Database: {args.database}")
        print(
            f"Packets: {stats.packets_read:,}; selected messages: {stats.selected_messages:,}; "
            f"snapshots: {stats.snapshots_written:,}; sequence gaps: {stats.sequence_gaps:,}; "
            f"missing messages: {stats.missing_messages:,}; replayed messages: "
            f"{stats.replayed_messages:,}; decode errors: {stats.decode_errors:,}; "
            f"book errors: {stats.book_errors:,}"
        )
        return 0
    if args.command == "query":
        try:
            snapshot_query = SnapshotQuery(
                symbol=args.symbol,
                order_book_id=args.order_book_id,
                sequence_number=args.sequence,
                start_ns=parse_time_ns(args.start) if args.start else None,
                end_ns=parse_time_ns(args.end) if args.end else None,
                limit=args.limit,
                latest=args.latest,
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
        snapshots = query_snapshots(SQLiteStore(args.database), snapshot_query)
        print(format_snapshots(snapshots))
        if args.output is not None:
            write_snapshot_csv(args.output, snapshots)
            print(f"CSV written: {args.output}")
        return 0
    if args.command == "analyze":
        if min(
            args.interval_ms,
            args.max_staleness_ms,
            args.momentum_periods,
            args.max_lag_steps,
        ) <= 0:
            raise SystemExit("analysis parameters must be positive")
        store = SQLiteStore(args.database)
        analyses = []
        for pair in load_symbol_pairs(args.pairs):
            analysis = analyze_pair(
                store,
                pair,
                interval_ms=args.interval_ms,
                max_staleness_ms=args.max_staleness_ms,
                momentum_periods=args.momentum_periods,
                max_lag_steps=args.max_lag_steps,
            )
            write_pair_reports(args.output, analysis)
            analyses.append(analysis)
            print(f"Analyzed {pair.name}: {len(analysis.observations):,} observations")
        summary_path = args.output / "summary.csv"
        write_analysis_summary(summary_path, analyses)
        print(f"Analysis summary written: {summary_path}")
        return 0
    return 2
