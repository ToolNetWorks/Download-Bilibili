"""ToolNet remote download worker."""

import logging
import os
import re
import secrets
from pathlib import Path
from threading import BoundedSemaphore

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from yt_dlp import YoutubeDL

from app.stream_ranges import RangeStreamer
from app.stream_mux import stream_mp4
from toolnet_bilibili.transfer_protocol import CONTENT_TYPE, encode_transfer


app = FastAPI(
    title="ToolNet Bilibili Download Worker",
    version="0.1.0",
)

logger = logging.getLogger(__name__)
api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
)

# One heavy download per small Northflank instance.
download_slot = BoundedSemaphore(1)

BVID_RE = re.compile(r"BV[A-Za-z0-9]{10}")


class DownloadRequest(BaseModel):
    url: str = Field(min_length=12, max_length=500)
    connections: int = Field(default=32, ge=1, le=100)
    max_height: int = Field(default=1080, ge=144, le=4320)


def require_api_key(
    provided: str | None = Security(api_key_header),
):
    expected = os.getenv(
        "DOWNLOAD_BILIBILI_API_KEY", ""
    )

    if not expected:
        raise HTTPException(
            503, "API authentication not configured"
        )

    if not provided or not secrets.compare_digest(
        provided, expected
    ):
        raise HTTPException(401, "Invalid API key")


def get_cookie_path():
    path = Path(
        os.getenv(
            "BILIBILI_COOKIE_FILE",
            "secrets/bilibili/cookies_bilibili.txt",
        )
    )

    if not path.is_file() or path.stat().st_size == 0:
        raise HTTPException(
            503, "Bilibili session unavailable"
        )

    return path


def extract_bvid(value):
    value = value.strip()

    if BVID_RE.fullmatch(value):
        return value

    match = re.fullmatch(
        r"https://(?:www\.)?bilibili\.com/video/"
        r"(BV[A-Za-z0-9]{10})/?(?:\?[^#]*)?",
        value,
    )

    if not match:
        raise HTTPException(
            422, "Invalid Bilibili BVID or URL"
        )

    return match.group(1)


def resolve_streams(bvid, max_height):
    options = {
        "cookiefile": str(get_cookie_path()),
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 25,
    }

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                f"https://www.bilibili.com/video/{bvid}",
                download=False,
            )
    except Exception as exc:
        logger.warning(
            "Resolve failed for %s: %s",
            bvid, type(exc).__name__,
        )
        raise HTTPException(
            502, "Cannot resolve Bilibili video"
        ) from None

    if not isinstance(info, dict):
        raise HTTPException(502, "Invalid video metadata")

    formats = [
        item for item in info.get("formats", [])
        if isinstance(item, dict)
        and str(item.get("url", "")).startswith("https://")
        and item.get("protocol") in ("http", "https")
    ]

    videos = [
        item for item in formats
        if str(item.get("vcodec", "")).startswith("avc1")
        and item.get("acodec") == "none"
        and (item.get("height") or 0) <= max_height
    ]

    audios = [
        item for item in formats
        if item.get("vcodec") == "none"
        and str(item.get("acodec", "")).startswith("mp4a")
    ]

    if not videos or not audios:
        raise HTTPException(
            422, "Compatible video/audio unavailable"
        )

    video = max(
        videos,
        key=lambda x: (
            x.get("height") or 0,
            x.get("tbr") or 0,
        ),
    )

    audio = max(
        audios,
        key=lambda x: x.get("abr") or x.get("tbr") or 0,
    )

    def prepare(item):
        headers = dict(info.get("http_headers") or {})
        headers.update(item.get("http_headers") or {})
        headers.setdefault(
            "Referer", "https://www.bilibili.com/"
        )
        return item["url"], headers

    return prepare(video), prepare(audio)


@app.get("/health")
def health():
    return {
        "success": True,
        "service": "download-bilibili",
        "status": "ready",
    }


@app.post(
    "/internal/v1/download/stream",
    dependencies=[Depends(require_api_key)],
)
def download_stream(request: DownloadRequest):
    bvid = extract_bvid(request.url)

    if not download_slot.acquire(blocking=False):
        raise HTTPException(
            429, "Downloader busy"
        )

    try:
        video, audio = resolve_streams(
            bvid, request.max_height
        )

        video_engine = RangeStreamer(
            video[0],
            video[1],
            connections=request.connections,
        )

        audio_engine = RangeStreamer(
            audio[0],
            audio[1],
            connections=min(request.connections, 16),
        )

        video_size = video_engine.probe_size()
        audio_size = audio_engine.probe_size()

    except Exception:
        download_slot.release()
        raise

    def generate():
        try:
            yield from encode_transfer(
                stream_mp4(
                    video_engine.iter_bytes(video_size),
                    audio_engine.iter_bytes(audio_size),
                )
            )
        except Exception:
            logger.exception(
                "Download stream failed for %s", bvid
            )
            raise
        finally:
            download_slot.release()

    return StreamingResponse(
        generate(),
        media_type=CONTENT_TYPE,
        headers={
            "Content-Disposition":
                f'attachment; filename="{bvid}.mp4"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
