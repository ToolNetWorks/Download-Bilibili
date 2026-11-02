"""Probe real Bilibili CDN with bounded HTTP Range requests."""

from urllib.parse import urlsplit

from app.stream_ranges import RangeStreamer
from server.main import resolve_streams


BVID = "BV1bn4P6bE59"
SAMPLE_BYTES = 64 * 1024


def inspect_source(label, source):
    url, headers = source

    engine = RangeStreamer(
        url=url,
        headers=headers,
        connections=4,
        chunk_mib=1,
    )

    total = engine.probe_size()

    if total <= 0:
        raise RuntimeError(f"{label}: invalid source size")

    first_end = min(SAMPLE_BYTES, total) - 1

    first, actual_total = engine._fetch(
        0,
        first_end,
        total,
    )

    if actual_total != total:
        raise RuntimeError(f"{label}: size mismatch")

    tail_start = max(0, total - SAMPLE_BYTES)

    tail, _ = engine._fetch(
        tail_start,
        total - 1,
        total,
    )

    box_type = (
        first[4:8].decode("ascii", "replace")
        if len(first) >= 8
        else "unknown"
    )

    print(f"{label} CDN: {urlsplit(url).hostname}")
    print(f"{label} TOTAL BYTES: {total}")
    print(f"{label} FIRST RANGE: {len(first)} bytes")
    print(f"{label} TAIL RANGE: {len(tail)} bytes")
    print(f"{label} FIRST BOX: {box_type}")

    if len(first) != first_end + 1:
        raise RuntimeError(f"{label}: incomplete first range")

    if len(tail) != total - tail_start:
        raise RuntimeError(f"{label}: incomplete tail range")

    print(f"{label} HTTP RANGE: PASS")


def main():
    print("===== RESOLVE REAL BILIBILI =====", flush=True)

    video, audio = resolve_streams(
        BVID,
        max_height=1080,
    )

    print("BILIBILI RESOLVE: PASS", flush=True)

    print("===== PROBE VIDEO CDN =====", flush=True)
    inspect_source("VIDEO", video)

    print("===== PROBE AUDIO CDN =====", flush=True)
    inspect_source("AUDIO", audio)

    print("REAL CDN RANGE TEST PASSED")


if __name__ == "__main__":
    main()
