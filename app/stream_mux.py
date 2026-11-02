"""Mux video and audio into fragmented MP4 without disk storage."""
import os
import queue
import subprocess
import threading
from collections import deque
from collections.abc import Iterable, Iterator
class StreamMuxError(RuntimeError):
    """FFmpeg streaming failure."""
def stream_mp4(
    video_chunks: Iterable[bytes],
    audio_chunks: Iterable[bytes],
    *,
    ffmpeg_bin: str = "ffmpeg",
) -> Iterator[bytes]:
    """Stream H.264 video and AAC audio as fragmented MP4."""
    video_read, video_write = os.pipe()
    audio_read, audio_write = os.pipe()
    command = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-thread_queue_size", "512",
        "-i", f"pipe:{video_read}",
        "-thread_queue_size", "512",
        "-i", f"pipe:{audio_read}",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "copy",
        "-movflags",
        "+frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        "pipe:1",
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(video_read, audio_read),
            bufsize=0,
        )
    except Exception:
        for fd in (
            video_read,
            video_write,
            audio_read,
            audio_write,
        ):
            os.close(fd)
        raise
    os.close(video_read)
    os.close(audio_read)
    stopped = threading.Event()
    errors = queue.Queue()
    stderr_tail = deque(maxlen=20)
    def feed(fd, chunks, label):
        try:
            for chunk in chunks:
                if stopped.is_set():
                    break
                if not isinstance(chunk, bytes):
                    raise TypeError(
                        f"{label} chunk must be bytes"
                    )
                view = memoryview(chunk)
                while view and not stopped.is_set():
                    written = os.write(fd, view)
                    if written <= 0:
                        raise StreamMuxError(
                            f"{label} pipe write failed"
                        )
                    view = view[written:]
        except BrokenPipeError:
            if not stopped.is_set():
                errors.put(
                    f"{label}: FFmpeg closed input pipe"
                )
        except Exception as exc:
            if not stopped.is_set():
                errors.put(
                    f"{label}: {type(exc).__name__}"
                )
        finally:
            close = getattr(chunks, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            try:
                os.close(fd)
            except OSError:
                pass
    def capture_stderr():
        if process.stderr is None:
            return
        for line in process.stderr:
            stderr_tail.append(
                line.decode(
                    "utf-8", "replace"
                ).strip()[:250]
            )
    threads = [
        threading.Thread(
            target=feed,
            args=(video_write, video_chunks, "video"),
            daemon=True,
        ),
        threading.Thread(
            target=feed,
            args=(audio_write, audio_chunks, "audio"),
            daemon=True,
        ),
        threading.Thread(
            target=capture_stderr,
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    try:
        if process.stdout is None:
            raise StreamMuxError(
                "FFmpeg stdout unavailable"
            )
        while True:
            data = process.stdout.read(256 * 1024)
            if not data:
                break
            yield data
        exit_code = process.wait(timeout=15)
        threads[0].join(timeout=3)
        threads[1].join(timeout=3)
        if not errors.empty():
            raise StreamMuxError(errors.get())
        if exit_code != 0:
            details = " | ".join(stderr_tail)
            raise StreamMuxError(
                f"FFmpeg exit {exit_code}: {details}"
            )
        if threads[0].is_alive() or threads[1].is_alive():
            raise StreamMuxError(
                "Input streaming did not finish"
            )
    finally:
        stopped.set()
        if process.poll() is None:
            process.kill()
        process.wait()
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
        for thread in threads:
            thread.join(timeout=1)
