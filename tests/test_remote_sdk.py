import tempfile
import unittest
from pathlib import Path
import httpx
from toolnet_bilibili.transfer_protocol import CONTENT_TYPE, encode_transfer
from toolnet_bilibili import (
    RemoteDownloader,
    RemoteDownloadError,
)
FAKE_MP4 = b"\x00\x00\x00\x18ftypisomFAKEMP4DATA"
class RemoteSDKTests(unittest.TestCase):
    def test_download_success(self):
        def handler(request):
            self.assertEqual(
                request.url.path,
                "/internal/v1/download/stream",
            )
            self.assertEqual(
                request.headers["X-API-Key"],
                "test-key",
            )
            return httpx.Response(
                200,
                headers={"Content-Type": CONTENT_TYPE},
                content=b''.join(encode_transfer([FAKE_MP4])),
            )
        client = RemoteDownloader(
            "https://example.com",
            "test-key",
            transport=httpx.MockTransport(handler),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "video.mp4"
            result = client.download(
                "BV1bn4P6bE59",
                output,
            )
            self.assertEqual(
                output.read_bytes(),
                FAKE_MP4,
            )
            self.assertEqual(
                result.bytes_received,
                len(FAKE_MP4),
            )
    def test_invalid_api_key(self):
        client = RemoteDownloader(
            "https://example.com",
            "wrong-key",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    401,
                    json={"detail": "Invalid API key"},
                )
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "video.mp4"
            with self.assertRaises(RemoteDownloadError):
                client.download("BV1bn4P6bE59", output)
            self.assertFalse(output.exists())
    def test_invalid_stream(self):
        client = RemoteDownloader(
            "https://example.com",
            "test-key",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    json={"error": "Invalid stream"},
                )
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "video.mp4"
            with self.assertRaises(RemoteDownloadError):
                client.download("BV1bn4P6bE59", output)
            self.assertFalse(output.exists())
if __name__ == "__main__":
    unittest.main(verbosity=2)
