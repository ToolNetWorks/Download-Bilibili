"""Integrity-framed protocol for remote media transfers."""

import hashlib
import re
import struct
from collections.abc import Iterable, Iterator
from typing import BinaryIO

MAGIC = b"TNBDL1\n"
CONTENT_TYPE = "application/vnd.toolnet.bilibili-stream"
MAX_DATA_FRAME = 1024 * 1024


class TransferError(RuntimeError):
    """Remote transfer failed or was incomplete."""


def _frame(kind: bytes, payload: bytes) -> bytes:
    return kind + struct.pack("!I", len(payload)) + payload


def encode_transfer(
    source: Iterable[bytes],
) -> Iterator[bytes]:
    """Produce data frames and a mandatory SHA-256 footer."""
    yield MAGIC

    digest = hashlib.sha256()
    total = 0

    try:
        for chunk in source:
            if not isinstance(chunk, bytes):
                raise TypeError("Stream chunk must be bytes")

            for offset in range(0, len(chunk), MAX_DATA_FRAME):
                part = chunk[offset:offset + MAX_DATA_FRAME]

                digest.update(part)
                total += len(part)

                yield _frame(b"D", part)

        footer = (
            f"{total}:{digest.hexdigest()}"
        ).encode("ascii")

        yield _frame(b"E", footer)

    except Exception as exc:
        # Do not expose private URLs or credentials.
        name = type(exc).__name__
        yield _frame(
            b"X",
            name.encode("ascii", "replace")[:64],
        )


class _Reader:
    def __init__(self, chunks: Iterable[bytes]):
        self.chunks = iter(chunks)
        self.buffer = bytearray()

    def read(self, count: int, *, optional=False):
        while len(self.buffer) < count:
            try:
                chunk = next(self.chunks)
            except StopIteration:
                if optional and not self.buffer:
                    return None

                raise TransferError(
                    "Incomplete remote stream"
                ) from None

            if not isinstance(chunk, bytes):
                raise TransferError(
                    "Invalid transport chunk"
                )

            self.buffer.extend(chunk)

        result = bytes(self.buffer[:count])
        del self.buffer[:count]

        return result


def decode_transfer(
    chunks: Iterable[bytes],
    output: BinaryIO,
) -> int:
    """Verify completion record before reporting success."""
    reader = _Reader(chunks)

    if reader.read(len(MAGIC)) != MAGIC:
        raise TransferError(
            "Unknown transfer protocol"
        )

    digest = hashlib.sha256()
    total = 0

    while True:
        kind = reader.read(1)

        declared = struct.unpack(
            "!I", reader.read(4)
        )[0]

        limit = (
            MAX_DATA_FRAME
            if kind == b"D"
            else 128
        )

        if declared > limit or (
            kind == b"D" and declared == 0
        ):
            raise TransferError(
                "Invalid frame length"
            )

        payload = reader.read(declared)

        if kind == b"D":
            output.write(payload)
            digest.update(payload)
            total += len(payload)
            continue

        if kind == b"X":
            raise TransferError(
                "Remote worker failed: "
                + payload.decode("ascii", "replace")
            )

        if kind != b"E":
            raise TransferError(
                "Unexpected record type"
            )

        match = re.fullmatch(
            rb"([0-9]+):([0-9a-f]{64})",
            payload,
        )

        if not match:
            raise TransferError(
                "Invalid completion record"
            )

        expected_size = int(match.group(1))
        expected_hash = match.group(2).decode()

        if (
            expected_size != total
            or expected_hash != digest.hexdigest()
        ):
            raise TransferError(
                "Transfer checksum or length mismatch"
            )

        if reader.read(1, optional=True) is not None:
            raise TransferError(
                "Unexpected data after completion"
            )

        return total
