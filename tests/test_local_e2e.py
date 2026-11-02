"""Test real H.264/AAC media through Worker -> SDK."""
import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
import server.main as worker
from toolnet_bilibili import RemoteDownloader
SOURCE = Path(
    "downloads/BV1bn4P6bE59.mp4"
).resolve()
def run(*args):
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-3000:])
    return result.stdout
def probe(path):
    return json.loads(
        run(
            "ffprobe",
            "-v", "error",
            "-show_streams",
            "-show_format",
            "-of", "json",
            str(path),
        )
    )
def codecs(path):
    info = probe(path)
    return {
        stream["codec_type"]: stream["codec_name"]
        for stream in info["streams"]
        if stream["codec_type"] in ("video", "audio")
    }
def create_samples(root):
    clip = root / "clip.mp4"
    video = root / "video.mp4"
    audio = root / "audio.m4a"
    run(
        "ffmpeg", "-y", "-v", "error",
        "-i", str(SOURCE),
        "-t", "8",
        "-map", "0:v:0",
        "-map", "0:a:0",
        "-c", "copy",
        str(clip),
    )
    run(
        "ffmpeg", "-y", "-v", "error",
        "-i", str(clip),
        "-map", "0:v:0",
        "-c", "copy",
        "-an",
        "-movflags", "+faststart",
        str(video),
    )
    run(
        "ffmpeg", "-y", "-v", "error",
        "-i", str(clip),
        "-map", "0:a:0",
        "-c", "copy",
        "-vn",
        "-movflags", "+faststart",
        str(audio),
    )
    return video, audio
class LocalRangeStreamer:
    """Fixture simulating verified CDN range output."""
    sources = {}
    def __init__(
        self,
        url,
        headers=None,
        connections=32,
        **kwargs,
    ):
        if url not in self.sources:
            raise ValueError("Unknown test URL")
        self.path = self.sources[url]
    def probe_size(self):
        return self.path.stat().st_size
    def iter_bytes(self, size=None):
        with self.path.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                yield chunk
def main():
    if not SOURCE.is_file():
        raise SystemExit(
            f"Missing source video: {SOURCE}"
        )
    print("===== REAL SOURCE =====", flush=True)
    source_codecs = codecs(SOURCE)
    print("CODECS:", source_codecs, flush=True)
    assert source_codecs == {
        "video": "h264",
        "audio": "aac",
    }, "Expected Bilibili H.264/AAC source"
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        print(
            "===== EXTRACT 8-SECOND CLIP =====",
            flush=True,
        )
        video, audio = create_samples(root)
        # Validate actual media packets, not only codec metadata.
        for label, path, stream_type in (
            ("VIDEO", video, "v:0"),
            ("AUDIO", audio, "a:0"),
        ):
            result = run(
                "ffprobe", "-v", "error",
                "-select_streams", stream_type,
                "-count_packets",
                "-show_entries", "stream=nb_read_packets",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ).strip()
            count = int(result) if result.isdigit() else 0
            print(f"{label} PACKETS: {count}", f"SIZE: {path.stat().st_size}", flush=True)
            if count < 10:
                raise RuntimeError(f"{label} source contains too few packets")
        LocalRangeStreamer.sources = {
            "https://test.local/video": video,
            "https://test.local/audio": audio,
        }
        streams = (
            ("https://test.local/video", {}),
            ("https://test.local/audio", {}),
        )
        with (
            patch.dict(
                os.environ,
                {"DOWNLOAD_BILIBILI_API_KEY": "local-test-key"},
            ),
            patch.object(
                worker,
                "resolve_streams",
                return_value=streams,
            ),
            patch.object(
                worker,
                "RangeStreamer",
                LocalRangeStreamer,
            ),
            TestClient(worker.app) as local_api,
        ):
            def bridge(request):
                response = local_api.post(
                    request.url.path,
                    content=request.content,
                    headers={
                        "X-API-Key": request.headers[
                            "X-API-Key"
                        ],
                        "Content-Type": "application/json",
                    },
                )
                return httpx.Response(
                    response.status_code,
                    headers={
                        "Content-Type":
                        response.headers.get(
                            "content-type", ""
                        ),
                    },
                    content=response.content,
                )
            downloader = RemoteDownloader(
                server="https://local.test",
                api_key="local-test-key",
                transport=httpx.MockTransport(bridge),
            )
            output = root / "result.mp4"
            print(
                "===== WORKER -> SDK =====",
                flush=True,
            )
            result = downloader.download(
                "BV1bn4P6bE59",
                output,
                connections=32,
            )
            print(
                "TRANSFER BYTES:",
                result.bytes_received,
            )
        print(
            "===== VERIFY OUTPUT =====",
            flush=True,
        )
        output_codecs = codecs(output)
        assert output_codecs == {
            "video": "h264",
            "audio": "aac",
        }, output_codecs
        run(
            "ffmpeg",
            "-hide_banner",
            "-v", "error",
            "-xerror",
            "-i", str(output),
            "-f", "null",
            "-",
        )
        print("H264 VIDEO: PASS")
        print("AAC AUDIO: PASS")
        print("SHA256 TRANSFER: PASS")
        print("FFMPEG DECODE: PASS")
        print("TEMPORARY FILE CLEANUP: PASS")
        print("LOCAL END-TO-END TEST PASSED")
if __name__ == "__main__":
    main()
