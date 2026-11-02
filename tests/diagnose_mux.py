"""Compare file-based and pipe-based H.264/AAC muxing."""

import json
import subprocess
import tempfile
from pathlib import Path

from app.stream_mux import stream_mp4


SOURCE = Path(
    "downloads/BV1bn4P6bE59.mp4"
).resolve()


def execute(*command):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode:
        raise RuntimeError(
            result.stderr[-2000:]
        )


def inspect(label, path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-count_packets",
            "-show_entries",
            "stream=codec_type,codec_name,"
            "nb_read_packets,start_time,duration",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    info = json.loads(result.stdout)

    print(f"\n{label}: {path.stat().st_size} bytes")

    for stream in info.get("streams", []):
        print(
            stream.get("codec_type"),
            stream.get("codec_name"),
            "packets=" + str(
                stream.get("nb_read_packets")
            ),
            "start=" + str(
                stream.get("start_time")
            ),
            "duration=" + str(
                stream.get("duration")
            ),
        )


def chunks(path):
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            yield chunk


def decode(path):
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner",
            "-v", "error",
            "-xerror",
            "-i", str(path),
            "-map", "0:v:0",
            "-map", "0:a:0",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode:
        print(
            "DECODE: FAIL",
            result.stderr[-500:],
        )
        return False

    print("DECODE: PASS")
    return True


def main():
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)

        clip = root / "clip.mp4"
        video = root / "video.mp4"
        audio = root / "audio.m4a"
        file_mux = root / "file_mux.mp4"
        pipe_mux = root / "pipe_mux.mp4"

        print("===== EXTRACT INPUT =====", flush=True)

        execute(
            "ffmpeg", "-y", "-v", "error",
            "-i", str(SOURCE),
            "-t", "8",
            "-map", "0:v:0",
            "-map", "0:a:0",
            "-c", "copy",
            str(clip),
        )

        execute(
            "ffmpeg", "-y", "-v", "error",
            "-i", str(clip),
            "-map", "0:v:0",
            "-an", "-c", "copy",
            "-movflags", "+faststart",
            str(video),
        )

        execute(
            "ffmpeg", "-y", "-v", "error",
            "-i", str(clip),
            "-map", "0:a:0",
            "-vn", "-c", "copy",
            "-movflags", "+faststart",
            str(audio),
        )

        inspect("VIDEO INPUT", video)
        inspect("AUDIO INPUT", audio)

        print(
            "\n===== TEST A: FILE MUX =====",
            flush=True,
        )

        execute(
            "ffmpeg", "-y",
            "-hide_banner", "-v", "error",
            "-i", str(video),
            "-i", str(audio),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c", "copy",
            "-movflags",
            "+frag_keyframe+empty_moov"
            "+default_base_moof",
            "-f", "mp4",
            str(file_mux),
        )

        inspect("FILE MUX OUTPUT", file_mux)
        file_ok = decode(file_mux)

        print(
            "\n===== TEST B: PIPE MUX =====",
            flush=True,
        )

        with pipe_mux.open("wb") as output:
            for chunk in stream_mp4(
                chunks(video),
                chunks(audio),
            ):
                output.write(chunk)

        inspect("PIPE MUX OUTPUT", pipe_mux)
        pipe_ok = decode(pipe_mux)

        print("\n===== DIAGNOSIS =====")

        if file_ok and pipe_ok:
            print("BOTH MUX METHODS: PASS")
            print(
                "Investigate Worker / SDK "
                "and E2E bridge next."
            )
            return

        if file_ok and not pipe_ok:
            print("FILE MUX: PASS")
            print("PIPE MUX: FAIL")
            print(
                "Focus on stream_mux.py "
                "and FFmpeg pipe behavior."
            )
            return

        if not file_ok:
            print("FILE MUX: FAIL")
            print(
                "Investigate timestamps, "
                "codec and source packets."
            )


if __name__ == "__main__":
    main()
