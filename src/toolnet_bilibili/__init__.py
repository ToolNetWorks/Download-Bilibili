"""ToolNet Bilibili remote downloader SDK."""
from .client import (
    DownloadResult,
    RemoteDownloader,
    RemoteDownloadError,
)
__version__ = "0.1.0"
__all__ = [
    "DownloadResult",
    "RemoteDownloader",
    "RemoteDownloadError",
]
