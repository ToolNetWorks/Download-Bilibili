"""Lightweight client for the remote Northflank downloader."""
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from .transfer_protocol import CONTENT_TYPE, TransferError, decode_transfer
class RemoteDownloadError(RuntimeError):
    """Remote downloader returned an error."""
@dataclass(frozen=True)
class DownloadResult:
    path: Path
    bytes_received: int
    connections: int
class RemoteDownloader:
    def __init__(
        self,
        server: str,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        server = server.rstrip("/")
        parsed = urlsplit(server)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("An HTTPS server is required")
        if parsed.username or parsed.password:
            raise ValueError("Credentials in URL are not allowed")
        if parsed.query or parsed.fragment:
            raise ValueError("Invalid server URL")
        if not api_key:
            raise ValueError("API key is required")
        self.server = server
        self.api_key = api_key
        self.transport = transport
    def download(
        self,
        url: str,
        output: str | Path,
        *,
        connections: int = 32,
        max_height: int = 1080,
    ) -> DownloadResult:
        if not url or not isinstance(url, str):
            raise ValueError("Bilibili URL or BVID required")
        if not 1 <= connections <= 100:
            raise ValueError("Connections must be 1-100")
        if not 144 <= max_height <= 4320:
            raise ValueError("Invalid max_height")
        destination = Path(output).expanduser().absolute()
        if destination.exists():
            raise FileExistsError(destination)
        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        endpoint = (
            self.server
            + "/internal/v1/download/stream"
        )
        timeout = httpx.Timeout(
            connect=20,
            read=180,
            write=30,
            pool=20,
        )
        fd, temporary_name = tempfile.mkstemp(
            prefix=".toolnet-",
            suffix=".part",
            dir=destination.parent,
        )
        temporary = Path(temporary_name)
        try:
            total = 0
            with os.fdopen(fd, "wb") as file:
                with httpx.Client(
                    timeout=timeout,
                    transport=self.transport,
                    follow_redirects=False,
                ) as client:
                    with client.stream(
                        "POST",
                        endpoint,
                        headers={
                            "X-API-Key": self.api_key,
                            "Accept": "video/mp4",
                        },
                        json={
                            "url": url,
                            "connections": connections,
                            "max_height": max_height,
                        },
                    ) as response:
                        if response.status_code != 200:
                            raise RemoteDownloadError(
                                "Northflank returned HTTP "
                                f"{response.status_code}"
                            )
                        content_type = response.headers.get(
                            "content-type", ""
                        ).lower()
                        if content_type.split(";", 1)[0].strip() != CONTENT_TYPE:
                            raise RemoteDownloadError(
                                "Unexpected response content type"
                            )
                        try:
                            total = decode_transfer(
                                response.iter_bytes(chunk_size=256 * 1024), file
                            )
                        except TransferError as exc:
                            raise RemoteDownloadError(
                                f"Transfer verification failed: {exc}"
                            ) from exc
                        file.flush()
                        os.fsync(file.fileno())
            with temporary.open("rb") as check_file:
                header = check_file.read(8)
            if total < 16 or header[4:8] != b"ftyp":
                raise RemoteDownloadError(
                    "Invalid or empty MP4 response"
                )
            if destination.exists():
                raise FileExistsError(destination)
            os.replace(temporary, destination)
            return DownloadResult(
                path=destination,
                bytes_received=total,
                connections=connections,
            )
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
