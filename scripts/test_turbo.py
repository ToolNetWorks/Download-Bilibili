import json
import sys
from pathlib import Path

from yt_dlp import YoutubeDL

ROOT = Path('/root/Download-Bilibili')
sys.path.insert(0, str(ROOT))
from app.range_engine import download

COOKIE = Path('/root/Download-Bilibili/secrets/bilibili/cookies_bilibili.txt')
BVID = 'BV1bn4P6bE59'


def main():
    if not COOKIE.is_file():
        raise RuntimeError('Bilibili cookie missing')
    options = {
        'cookiefile': str(COOKIE), 'quiet': True, 'no_warnings': True,
        'skip_download': True, 'noplaylist': True, 'socket_timeout': 20,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(f'https://www.bilibili.com/video/{BVID}', download=False)
    video_formats = [item for item in info.get('formats', [])
                     if item.get('url') and item.get('protocol') in ('https', 'http')
                     and item.get('vcodec') not in (None, 'none')
                     and item.get('acodec') == 'none']
    if not video_formats:
        raise RuntimeError('No DASH video stream found')
    selected = max(video_formats, key=lambda item: (item.get('height') or 0, item.get('tbr') or 0))
    headers = dict(info.get('http_headers') or {})
    headers.update(selected.get('http_headers') or {})
    headers.setdefault('Referer', 'https://www.bilibili.com/')
    result = download(url=selected['url'], output=ROOT / 'tmp/turbo_sample.bin',
                      headers=headers, stream_id=f"{BVID}_{selected['format_id']}",
                      connections=32, chunk_mib=8, sample_mib=256)
    print('\n===== TURBO DOWNLOAD RESULT =====')
    print(json.dumps(result, indent=2))
    print('TEST COMPLETED')


if __name__ == '__main__':
    main()
