import concurrent.futures
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from yt_dlp import YoutubeDL

COOKIE_FILE = Path(
    "/root/Download-Bilibili/"
    "secrets/bilibili/cookies_bilibili.txt"
)

BVID = "BV1bn4P6bE59"

CONCURRENCY_LEVELS = [16, 32, 64, 100]
SAMPLE_BYTES = 32 * 1024 * 1024


def get_video_stream():
    if not COOKIE_FILE.is_file():
        raise RuntimeError("Bilibili cookie not found")

    options = {
        "cookiefile": str(COOKIE_FILE),
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
        item
        for item in info.get("formats", [])
        if item.get("url")
        and item.get("protocol", "").startswith("http")
        and item.get("vcodec") not in (None, "none")
    ]

    if not formats:
        raise RuntimeError("No HTTP video format")

    selected = max(
        formats,
        key=lambda item: (
            item.get("height") or 0,
            item.get("tbr") or 0,
        ),
    )

    headers = dict(
        info.get("http_headers") or {}
    )
    headers.update(
        selected.get("http_headers") or {}
    )
    headers.setdefault(
        "Referer",
        "https://www.bilibili.com/",
    )
    headers["Accept-Encoding"] = "identity"

    return selected["url"], headers


def download_range(url, headers, start, end):
    request_headers = dict(headers)
    request_headers["Range"] = (
        f"bytes={start}-{end}"
    )

    request = urllib.request.Request(
        url,
        headers=request_headers,
    )

    with urllib.request.urlopen(
        request, timeout=30
    ) as response:

        if response.status != 206:
            raise RuntimeError(
                f"Expected 206, got {response.status}"
            )

        content_range = response.headers.get(
            "Content-Range", ""
        )

        match = re.fullmatch(
            r"bytes (\d+)-(\d+)/(\d+|\*)",
            content_range,
        )

        if not match:
            raise RuntimeError(
                "Invalid Content-Range"
            )

        if (
            int(match.group(1)) != start
            or int(match.group(2)) != end
        ):
            raise RuntimeError(
                "Unexpected byte range"
            )

        total = 0

        while True:
            chunk = response.read(128 * 1024)

            if not chunk:
                break

            total += len(chunk)

        expected = end - start + 1

        if total != expected:
            raise RuntimeError(
                f"Incomplete: {total}/{expected}"
            )

        return total


def benchmark(url, headers, connections):
    chunk_size = (
        SAMPLE_BYTES + connections - 1
    ) // connections

    ranges = []

    for index in range(connections):
        start = index * chunk_size
        end = min(
            SAMPLE_BYTES - 1,
            start + chunk_size - 1,
        )

        if start <= end:
            ranges.append((start, end))

    start_time = time.perf_counter()
    completed = 0

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=connections
    ) as executor:

        futures = [
            executor.submit(
                download_range,
                url,
                headers,
                start,
                end,
            )
            for start, end in ranges
        ]

        for future in concurrent.futures.as_completed(
            futures
        ):
            completed += future.result()

    elapsed = time.perf_counter() - start_time

    speed = (
        completed / 1024 / 1024 / elapsed
    )

    return {
        "connections": connections,
        "seconds": round(elapsed, 2),
        "mbps": round(speed, 2),
        "bytes": completed,
    }


def main():
    print(
        "===== BILIBILI DOWNLOAD BENCHMARK =====",
        flush=True,
    )

    url, headers = get_video_stream()
    results = []

    for connections in CONCURRENCY_LEVELS:
        print(
            f"TESTING: {connections} connections",
            flush=True,
        )

        try:
            result = benchmark(
                url,
                headers,
                connections,
            )
        except Exception as exc:
            print(
                "FAILED:",
                connections,
                type(exc).__name__,
                str(exc).splitlines()[0][:150],
                flush=True,
            )
            # Stop on CDN rejection instead of
            # generating more concurrent requests.
            break

        results.append(result)

        print(
            f"{connections} connections: "
            f"{result['mbps']} MiB/s "
            f"({result['seconds']}s)",
            flush=True,
        )

    print("\n===== RESULTS =====")

    for result in results:
        print(
            result["connections"],
            "connections:",
            result["mbps"],
            "MiB/s",
        )

    if results:
        fastest = max(
            results,
            key=lambda item: item["mbps"],
        )

        print(
            "FASTEST TESTED:",
            fastest["connections"],
            "connections",
        )
        print(
            "SPEED:",
            fastest["mbps"],
            "MiB/s",
        )

    print("BENCHMARK COMPLETED")


if __name__ == "__main__":
    main()
