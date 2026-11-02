import io
import unittest

from toolnet_bilibili.transfer_protocol import (
    TransferError,
    decode_transfer,
    encode_transfer,
)


class TransferProtocolTests(unittest.TestCase):

    def test_complete_transfer(self):
        original = b"video-data-" * 10000
        encoded = b"".join(encode_transfer([original]))
        output = io.BytesIO()

        chunks = (
            encoded[i:i + 7]
            for i in range(0, len(encoded), 7)
        )

        received = decode_transfer(chunks, output)

        self.assertEqual(received, len(original))
        self.assertEqual(output.getvalue(), original)

    def test_connection_closed_early(self):
        frames = list(
            encode_transfer([b"partial-video"])
        )

        with self.assertRaises(TransferError):
            decode_transfer(
                frames[:-1],
                io.BytesIO(),
            )

    def test_corrupted_data(self):
        encoded = bytearray(
            b"".join(
                encode_transfer([b"original-video"])
            )
        )

        position = encoded.index(b"original-video")
        encoded[position] ^= 1

        with self.assertRaises(TransferError):
            decode_transfer(
                [bytes(encoded)],
                io.BytesIO(),
            )

    def test_remote_worker_failure(self):
        def broken_source():
            yield b"partial-data"
            raise OSError("Simulated network failure")

        with self.assertRaises(TransferError):
            decode_transfer(
                encode_transfer(broken_source()),
                io.BytesIO(),
            )

    def test_unexpected_extra_data(self):
        encoded = b"".join(
            encode_transfer([b"video"])
        )

        with self.assertRaises(TransferError):
            decode_transfer(
                [encoded + b"unexpected"],
                io.BytesIO(),
            )

    def test_large_payload(self):
        original = b"X" * (3 * 1024 * 1024 + 17)
        output = io.BytesIO()

        received = decode_transfer(
            encode_transfer([original]),
            output,
        )

        self.assertEqual(received, len(original))
        self.assertEqual(output.getvalue(), original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
