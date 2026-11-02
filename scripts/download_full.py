import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from yt_dlp import YoutubeDL

ROOT = Path("/root/Download-Bilibili")
sys.path.insert(0, str(ROOT))

from app.range_engine import download

COOKIE = ROOT / "secrets/bilibili/cookies_bilibili.txt"
OUTPUT_DIR = ROOT / "downloads"


def extract_bvid(value):
    match = re.search(r"BV[A-Za-z0-9]{10}", value)
    if not match:
        raise ValueError("Invalid Bilibili BVID or URL")
    return match.group(0)


def select_streams(info, max_height):
    formats = info.get("formats") or []

    videos = [
        f for f in formats
        if f.get("url")
        and f.get("vcodec") not in (None, "none")
        and f.get("acodec") == "none"
        and f.get("protocol") in ("http", "https")
        and (f.get("height") or 0) <= max_height
    ]

    audios = [
        f for f in formats
        if f.get("url")
        and f.get("vcodec") == "none"
        and f.get("acodec") not in (None, "none")
        and f.get("protocol") in ("http", "https")
    ]

    if not videos:
        raise RuntimeError("No compatible video stream")

    if not audios:
        raise RuntimeError("No separate audio stream")

    video = max(
        videos,
        key=lambda f: (
            f.get("height") or 0,
            f.get("tbr") or 0,
        ),
    )

    audio = max(
        audios,
        key=lambda f: f.get("abr") or f.get("tbr") or 0,
    )

    return video, audio


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument(
        "--connections",
        type=int,
        default=32,
    )
    parser.add_argument(
        "--chunk-mib",
        type=int,
        default=8,
    )
    parser.add_argument(
        "--max-height",
        type=int,
        default=1080,
    )
    parser.add_argument(
        "--info",
        action="store_true",
    )

    args = parser.parse_args()

    if not 1 <= args.connections <= 100:
        parser.error("connections must be 1-100")

    if not 1 <= args.chunk_mib <= 128:
        parser.error("chunk-mib must be 1-128")

    if args.max_height < 144:
        parser.error("max-height must be >= 144")

    if not COOKIE.is_file():
        raise RuntimeError("Bilibili cookie missing")

    bvid = extract_bvid(args.url)

    options = {
        "cookiefile": str(COOKIE),
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 25,
    }

    print("===== RESOLVE BILIBILI =====", flush=True)

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(
            f"https://www.bilibili.com/video/{bvid}",
            download=False,
        )

    if not isinstance(info, dict):
        raise RuntimeError("Cannot extract metadata")

    video, audio = select_streams(
        info,
        args.max_height,
    )

    print("BVID:", bvid)
    print("TITLE:", info.get("title"))
    print("VIDEO FORMAT:", video.get("format_id"))
    print("RESOLUTION:", video.get("height"))
    print("VIDEO CODEC:", video.get("vcodec"))
    print("AUDIO FORMAT:", audio.get("format_id"))
    print("AUDIO CODEC:", audio.get("acodec"))
    print("CONNECTIONS:", args.connections)

    if args.info:
        print("METADATA READY")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    video_path = OUTPUT_DIR / f"{bvid}.video.m4s"
    audio_path = OUTPUT_DIR / f"{bvid}.audio.m4s"
    final_path = OUTPUT_DIR / f"{bvid}.mp4"
    temporary_mp4 = OUTPUT_DIR / f"{bvid}.merging.mp4"

    if final_path.exists():
        raise RuntimeError(
            f"Output already exists: {final_path}"
        )

    def headers_for(stream):
        headers = dict(info.get("http_headers") or {})
        headers.update(stream.get("http_headers") or {})
        headers.setdefault(
            "Referer",
            "https://www.bilibili.com/",
        )
        return headers

    for label, stream, path in (
        ("VIDEO", video, video_path),
        ("AUDIO", audio, audio_path),
    ):
        print(f"\n===== DOWNLOAD {label} =====", flush=True)

        if path.exists():
            raise RuntimeError(
                f"Existing stream file: {path}"
            )

        result = download(
            url=stream["url"],
            output=path,
            headers=headers_for(stream),
            stream_id=f"{bvid}_{label}_{stream['format_id']}",
            connections=args.connections,
            chunk_mib=args.chunk_mib,
        )

        print(
            json.dumps(result, indent=2),
            flush=True,
        )

    print("\n===== FFMPEG MERGE =====", flush=True)

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c", "copy",
        "-movflags", "+faststart",
        str(temporary_mp4),
    ]

    subprocess.run(command, check=True)

    subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration,size",
            "-of", "json",
            str(temporary_mp4),
        ],
        check=True,
    )

    temporary_mp4.replace(final_path)

    print("\n===== DOWNLOAD COMPLETED =====")
    print("FILE:", final_path)
    print(
        "SIZE MiB:",
        round(final_path.stat().st_size / 1048576, 2),
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            "DOWNLOAD FAILED:",
            type(exc).__name__,
            str(exc).splitlines()[0][:200],
            file=sys.stderr,
        )
        sys.exit(1)
