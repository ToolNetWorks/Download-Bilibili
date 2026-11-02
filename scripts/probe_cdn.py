import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL

COOKIE = Path(
    "/root/Download-Bilibili/"
    "secrets/bilibili/cookies_bilibili.txt"
)

BVID = "BV1bn4P6bE59"


def main():
    if not COOKIE.is_file():
        print("ERROR: Cookie Bilibili not found")
        return 1

    print("===== BILIBILI CDN PROBE =====", flush=True)

    options = {
        "cookiefile": str(COOKIE),
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
    }

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(
            f"https://www.bilibili.com/video/{BVID}",
            download=False,
        )

    formats = [
        item for item in info.get("formats", [])
        if item.get("url")
        and item.get("protocol", "").startswith("http")
        and item.get("vcodec") not in (None, "none")
    ]

    if not formats:
        print("ERROR: No HTTP video streams available")
        return 1

    video = max(
        formats,
        key=lambda item: (
            item.get("height") or 0,
            item.get("tbr") or 0,
        ),
    )

    url = video["url"]
    headers = dict(video.get("http_headers") or {})

    headers.setdefault(
        "User-Agent",
        "Mozilla/5.0",
    )
    headers.setdefault(
        "Referer",
        "https://www.bilibili.com/",
    )
    headers["Range"] = "bytes=0-1023"
    headers["Accept-Encoding"] = "identity"

    print("BVID:", BVID)
    print("FORMAT:", video.get("format_id"))
    print("RESOLUTION:", video.get("height"))
    print("CDN HOST:", urlsplit(url).hostname)
    print("PROTOCOL:", video.get("protocol"))
    print("FILE SIZE:", video.get("filesize") or "UNKNOWN")

    request = urllib.request.Request(
        url,
        headers=headers,
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=20,
        ) as response:
            status = response.status
            content_range = response.headers.get(
                "Content-Range", ""
            )

            print("HTTP STATUS:", status)
            print("CONTENT RANGE:", content_range or "NONE")

            if status == 206 and content_range.startswith("bytes "):
                response.read(1024)
                print("RANGE SUPPORTED: YES")
                print("100-CONNECTION ENGINE: POSSIBLE")
                return 0

            print("RANGE SUPPORTED: NOT CONFIRMED")
            return 2

    except urllib.error.HTTPError as exc:
        print("HTTP STATUS:", exc.code)
        print("RANGE SUPPORTED: NOT CONFIRMED")
        return 2

    except (OSError, TimeoutError) as exc:
        print("NETWORK ERROR:", type(exc).__name__)
        return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("PROBE FAILED:", type(exc).__name__)
        sys.exit(1)
