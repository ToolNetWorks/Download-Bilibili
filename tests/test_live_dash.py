"""Inspect real Bilibili DASH media without downloading full files."""

import json
import subprocess

from app.stream_ranges import RangeStreamer
from server.main import resolve_streams


BVID = "BV1bn4P6bE59"
MIB = 1024 * 1024


def fetch_sample(source, limit_mib):
    url, headers = source

    engine = RangeStreamer(
        url=url,
        headers=headers,
        connections=4,
        chunk_mib=1,
    )

    total = engine.probe_size()
    limit = min(total, limit_mib * MIB)

    parts = []

    for start in range(0, limit, MIB):
        end = min(start + MIB, limit) - 1

        data, actual_total = engine._fetch(
            start, end, total
        )

        if actual_total != total:
            raise RuntimeError("Source size changed")

        parts.append(data)

    return b"".join(parts)


def analyze(label, source, limit_mib, expected_codec):
    print(f"===== {label} =====", flush=True)

    sample = fetch_sample(source, limit_mib)

    first_box = (
        sample[4:8].decode("ascii", "replace")
        if len(sample) >= 8
        else "unknown"
    )

    print("SAMPLED BYTES:", len(sample))
    print("FIRST BOX:", first_box)

    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-probesize", "5000000",
            "-analyzeduration", "5000000",
            "-count_packets",
            "-show_entries",
            "stream=codec_type,codec_name,nb_read_packets",
            "-of", "json",
            "-i", "pipe:0",
        ],
        input=sample,
        capture_output=True,
        timeout=60,
    )

    if result.returncode:
        print(
            "FFPROBE WARNING:",
            result.stderr.decode(
                "utf-8", "replace"
            )[-1000:],
        )

    try:
        metadata = json.loads(
            result.stdout.decode("utf-8")
        )
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError(
            f"{label}: cannot parse FFprobe result"
        )

    streams = metadata.get("streams", [])

    for stream in streams:
        print("STREAM:", stream)

    valid = [
        stream for stream in streams
        if stream.get("codec_name") == expected_codec
        and int(stream.get("nb_read_packets") or 0) > 0
    ]

    if not valid:
        raise RuntimeError(
            f"{label}: no {expected_codec} packets detected"
        )

    print(f"{label} DASH PACKETS: PASS")


def main():
    print("===== RESOLVE BILIBILI =====", flush=True)

    video, audio = resolve_streams(
        BVID, max_height=1080
    )

    analyze("VIDEO", video, 12, "h264")
    analyze("AUDIO", audio, 2, "aac")

    print("REAL DASH INPUT TEST PASSED")


if __name__ == "__main__":
    main()
