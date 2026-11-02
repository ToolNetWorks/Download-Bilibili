"""Test real CDN Range -> FFmpeg fragmented MP4."""

import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.stream_mux import stream_mp4
from app.stream_ranges import RangeStreamer
from server.main import resolve_streams


BVID = "BV1bn4P6bE59"
MIB = 1024 * 1024


def limited_chunks(source, limit_mib):
    url, headers = source

    engine = RangeStreamer(
        url=url,
        headers=headers,
        connections=4,
        chunk_mib=1,
    )

    total = engine.probe_size()
    limit = min(total, limit_mib * MIB)

    print(
        f"READING {limit / MIB:.1f} MiB",
        flush=True,
    )

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = []

        for start in range(0, limit, MIB):
            end = min(start + MIB, limit) - 1

            futures.append(
                executor.submit(
                    engine._fetch,
                    start,
                    end,
                    total,
                )
            )

        for future in futures:
            data, actual_total = future.result()

            if actual_total != total:
                raise RuntimeError("CDN source size changed")

            yield data


def probe(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-count_packets",
            "-show_entries",
            "stream=codec_type,codec_name,nb_read_packets",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode:
        raise RuntimeError(
            "FFprobe failed: "
            + result.stderr[-500:]
        )

    return json.loads(result.stdout)


def decode_sample(path):
    return subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-v", "error",
            "-xerror",
            "-i", str(path),
            "-t", "3",
            "-map", "0:v:0",
            "-map", "0:a:0",
            "-f", "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


def main():
    print("===== RESOLVE REAL CDN =====", flush=True)

    video, audio = resolve_streams(
        BVID,
        max_height=1080,
    )

    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "live-test.mp4"

        print(
            "===== CDN RANGE -> FFMPEG =====",
            flush=True,
        )

        mux_error = None

        with output.open("wb") as file:
            try:
                for chunk in stream_mp4(
                    limited_chunks(video, 12),
                    limited_chunks(audio, 2),
                ):
                    file.write(chunk)

            except Exception as exc:
                mux_error = (
                    f"{type(exc).__name__}: {exc}"
                )

        print(
            "OUTPUT BYTES:",
            output.stat().st_size,
            flush=True,
        )

        if mux_error:
            print("MUX ERROR:", mux_error[:1000])

        if output.stat().st_size < 100_000:
            raise RuntimeError(
                "No meaningful MP4 media payload produced"
            )

        print(
            "===== VALIDATE MP4 =====",
            flush=True,
        )

        info = probe(output)

        streams = {
            stream["codec_type"]: stream
            for stream in info.get("streams", [])
            if stream.get("codec_type")
            in ("video", "audio")
        }

        for kind in ("video", "audio"):
            stream = streams.get(kind, {})

            print(
                kind.upper(),
                "CODEC:",
                stream.get("codec_name"),
                "PACKETS:",
                stream.get("nb_read_packets"),
            )

        video_packets = int(
            streams.get("video", {}).get(
                "nb_read_packets"
            ) or 0
        )

        audio_packets = int(
            streams.get("audio", {}).get(
                "nb_read_packets"
            ) or 0
        )

        if video_packets == 0 or audio_packets == 0:
            raise RuntimeError(
                "Missing output video/audio packets"
            )

        result = decode_sample(output)

        if result.returncode:
            print(
                "DECODE ERROR:",
                result.stderr[-1000:],
            )
            raise RuntimeError(
                "First 3 seconds failed to decode"
            )

        print("VIDEO PACKETS: PASS")
        print("AUDIO PACKETS: PASS")
        print("FIRST 3 SECONDS DECODE: PASS")

        if mux_error:
            print(
                "PARTIAL DASH MUX: MEDIA VALID, "
                "BUT STREAM ENDED WITH ERROR"
            )
            print(
                "Full-length CDN test still required."
            )
            return

        print("REAL CDN MUX TEST PASSED")


if __name__ == "__main__":
    main()
