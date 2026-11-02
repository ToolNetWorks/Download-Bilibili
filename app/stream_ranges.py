"""Bounded-memory HTTP Range streaming engine."""

import concurrent.futures
import re
import time
import urllib.error
import urllib.request
from collections import deque

RANGE_PATTERN = re.compile(
    r"bytes (\d+)-(\d+)/(\d+)"
)

RETRYABLE = {408, 429, 500, 502, 503, 504}


class RangeDownloadError(RuntimeError):
    pass


class RangeStreamer:
    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        connections: int = 32,
        chunk_mib: int = 1,
        timeout: int = 25,
    ):
        if not url.startswith("https://"):
            raise ValueError("HTTPS source URL required")

        if not 1 <= connections <= 100:
            raise ValueError("connections must be 1..100")

        if not 1 <= chunk_mib <= 2:
            raise ValueError("chunk_mib must be 1..2")

        self.url = url
        self.headers = dict(headers or {})
        self.connections = connections
        self.chunk_bytes = chunk_mib * 1024 * 1024
        self.timeout = timeout

    def _fetch(self, start, end, total=None):
        headers = dict(self.headers)

        headers["Range"] = f"bytes={start}-{end}"
        headers["Accept-Encoding"] = "identity"

        request = urllib.request.Request(
            self.url,
            headers=headers,
        )

        expected = end - start + 1

        for attempt in range(3):
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.timeout,
                ) as response:

                    if response.status != 206:
                        raise RangeDownloadError(
                            "Expected HTTP 206"
                        )

                    value = response.headers.get(
                        "Content-Range", ""
                    )

                    match = RANGE_PATTERN.fullmatch(value)

                    if not match:
                        raise RangeDownloadError(
                            "Invalid Content-Range"
                        )

                    actual_start, actual_end, actual_total = (
                        map(int, match.groups())
                    )

                    if (actual_start, actual_end) != (start, end):
                        raise RangeDownloadError(
                            "Byte range mismatch"
                        )

                    if total is not None and actual_total != total:
                        raise RangeDownloadError(
                            "Source size changed"
                        )

                    data = response.read(expected + 1)

                    if len(data) != expected:
                        raise RangeDownloadError(
                            "Incomplete chunk"
                        )

                    return data, actual_total

            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE:
                    raise RangeDownloadError(
                        f"CDN HTTP {exc.code}"
                    ) from exc

                if attempt == 2:
                    raise RangeDownloadError(
                        f"CDN retries exhausted: {exc.code}"
                    ) from exc

            except (OSError, TimeoutError) as exc:
                if attempt == 2:
                    raise RangeDownloadError(
                        "Network retries exhausted"
                    ) from exc

            time.sleep(min(2 ** attempt, 4))

        raise RangeDownloadError("Download failed")

    def probe_size(self):
        _, total = self._fetch(0, 0)

        if total <= 0:
            raise RangeDownloadError("Empty source")

        return total

    def iter_bytes(self, size=None):
        """Yield ordered chunks with bounded prefetch."""

        total = size if size is not None else self.probe_size()

        if total <= 0:
            raise ValueError("Invalid source size")

        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.connections
        )

        pending = deque()
        next_start = 0

        try:
            while next_start < total or pending:

                while (
                    len(pending) < self.connections
                    and next_start < total
                ):
                    start = next_start

                    end = min(
                        start + self.chunk_bytes,
                        total,
                    ) - 1

                    future = executor.submit(
                        self._fetch,
                        start,
                        end,
                        total,
                    )

                    pending.append(future)
                    next_start = end + 1

                data, _ = pending.popleft().result()

                yield data

        finally:
            for future in pending:
                future.cancel()

            executor.shutdown(
                wait=False,
                cancel_futures=True,
            )
