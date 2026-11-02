import concurrent.futures
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

RANGE_PATTERN = re.compile(r"bytes (\d+)-(\d+)/(\d+)")


class DownloadError(RuntimeError):
    pass


def open_range(url, headers, start, end):
    request_headers = dict(headers)
    request_headers['Range'] = f'bytes={start}-{end}'
    request_headers['Accept-Encoding'] = 'identity'
    request = urllib.request.Request(url, headers=request_headers)
    return urllib.request.urlopen(request, timeout=35)


def probe_size(url, headers):
    with open_range(url, headers, 0, 0) as response:
        if response.status != 206:
            raise DownloadError('CDN does not support Range')
        match = RANGE_PATTERN.fullmatch(response.headers.get('Content-Range', ''))
        if not match:
            raise DownloadError('Invalid Content-Range')
        start, end, total = map(int, match.groups())
        if start != 0 or end != 0:
            raise DownloadError('Unexpected probe range')
        if len(response.read(2)) != 1:
            raise DownloadError('Invalid probe response')
        return total


def fetch_chunk(url, headers, start, end, total, path):
    expected = end - start + 1
    temporary = path.with_suffix('.writing')
    for attempt in range(4):
        try:
            with open_range(url, headers, start, end) as response:
                if response.status != 206:
                    raise DownloadError('Expected HTTP 206')
                match = RANGE_PATTERN.fullmatch(response.headers.get('Content-Range', ''))
                if not match:
                    raise DownloadError('Invalid range header')
                if tuple(map(int, match.groups())) != (start, end, total):
                    raise DownloadError('Range mismatch')
                received = 0
                with temporary.open('wb') as file:
                    while received < expected:
                        block = response.read(min(256 * 1024, expected - received))
                        if not block:
                            break
                        file.write(block)
                        received += len(block)
                if received != expected:
                    raise DownloadError('Incomplete chunk')
            os.replace(temporary, path)
            return expected
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 412, 416):
                raise DownloadError(f'CDN HTTP {exc.code}') from exc
            if attempt == 3:
                raise DownloadError(f'HTTP {exc.code} after retries') from exc
        except (OSError, DownloadError) as exc:
            if attempt == 3:
                raise DownloadError(f'Chunk failed: {type(exc).__name__}') from exc
        finally:
            temporary.unlink(missing_ok=True)
        time.sleep(min(2 ** attempt, 8))
    raise DownloadError('Retry failed')


def download(url, output, headers, stream_id, connections=32, chunk_mib=8, sample_mib=None):
    if not 1 <= connections <= 100:
        raise ValueError('Connections must be 1-100')
    if not 1 <= chunk_mib <= 128:
        raise ValueError('Chunk size must be 1-128 MiB')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', stream_id):
        raise ValueError('Invalid stream ID')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    total = probe_size(url, headers)
    size = total
    if sample_mib is not None:
        if sample_mib < 1:
            raise ValueError('Invalid sample size')
        size = min(total, sample_mib * 1024 * 1024)
    chunk_bytes = chunk_mib * 1024 * 1024
    chunks = [(index, start, min(start + chunk_bytes - 1, size - 1))
              for index, start in enumerate(range(0, size, chunk_bytes))]
    workdir = output.parent / (output.name + '.chunks')
    workdir.mkdir(parents=True, exist_ok=True)
    manifest_path = workdir / 'manifest.json'
    manifest = {'stream_id': stream_id, 'total': total, 'size': size, 'chunk_bytes': chunk_bytes}
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding='utf-8'))
        if previous != manifest:
            raise DownloadError('Resume metadata mismatch')
    else:
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    if shutil.disk_usage(output.parent).free < 2 * size + 128 * 1024 * 1024:
        raise DownloadError('Insufficient disk space')
    pending = []
    resumed = 0
    for index, start, end in chunks:
        path = workdir / f'{index:06d}.part'
        expected = end - start + 1
        if path.exists() and path.stat().st_size == expected:
            resumed += 1
            continue
        path.unlink(missing_ok=True)
        pending.append((start, end, path))
    print('TOTAL MiB:', round(total / 1048576, 2))
    print('DOWNLOAD MiB:', round(size / 1048576, 2))
    print('CHUNKS:', len(chunks))
    print('RESUMED:', resumed)
    print('CONNECTIONS:', min(connections, len(pending)))
    print('STARTING DOWNLOAD', flush=True)
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=connections) as executor:
        futures = [executor.submit(fetch_chunk, url, headers, start, end, total, path)
                   for start, end, path in pending]
        for count, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            try:
                future.result()
            except Exception:
                for task in futures:
                    task.cancel()
                raise
            if count % 8 == 0 or count == len(futures):
                print(f'FINISHED {resumed + count}/{len(chunks)}', flush=True)
    assembling = output.with_suffix(output.suffix + '.assembling')
    with assembling.open('wb') as destination:
        for index, start, end in chunks:
            with (workdir / f'{index:06d}.part').open('rb') as source:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
    if assembling.stat().st_size != size:
        raise DownloadError('Assembled size mismatch')
    os.replace(assembling, output)
    shutil.rmtree(workdir)
    elapsed = time.perf_counter() - started
    speed = size / max(elapsed, 0.001) / 1048576
    return {'success': True, 'file': str(output), 'size_mib': round(size / 1048576, 2),
            'seconds': round(elapsed, 2), 'speed_mib_s': round(speed, 2),
            'connections': connections, 'chunks': len(chunks)}
