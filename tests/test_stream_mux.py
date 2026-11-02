"""Test FFmpeg pipe streaming with video and audio."""
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from app.stream_mux import stream_mp4

def run(command):
    subprocess.run(command, check=True, capture_output=True)

def read_chunks(path):
    with path.open('rb') as file:
        while True:
            chunk = file.read(64 * 1024)
            if not chunk:
                return
            yield chunk

def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        video_path = root / 'video.mp4'
        audio_path = root / 'audio.m4a'
        output_path = root / 'result.mp4'
        print('===== CREATE TEST MEDIA =====', flush=True)
        run([
            'ffmpeg', '-y', '-v', 'error',
            '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25',
            '-t', '3', '-c:v', 'mpeg4', '-q:v', '8',
            '-movflags', '+faststart', str(video_path),
        ])
        run([
            'ffmpeg', '-y', '-v', 'error',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100',
            '-t', '3', '-c:a', 'aac', '-b:a', '128k',
            '-movflags', '+faststart', str(audio_path),
        ])
        print('===== STREAMING FFMPEG =====', flush=True)
        with output_path.open('wb') as output:
            for chunk in stream_mp4(read_chunks(video_path), read_chunks(audio_path)):
                output.write(chunk)
        print('===== VALIDATE MP4 =====', flush=True)
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type',
            '-show_entries', 'format=duration', '-of', 'json', str(output_path),
        ], check=True, capture_output=True, text=True)
        metadata = json.loads(result.stdout)
        stream_types = {stream['codec_type'] for stream in metadata['streams']}
        duration = float(metadata['format']['duration'])
        if stream_types != {'video', 'audio'}:
            raise RuntimeError(f'Missing streams: {stream_types}')
        if not 2.5 <= duration <= 3.5:
            raise RuntimeError(f'Unexpected duration: {duration}')
        print('VIDEO: PASS')
        print('AUDIO: PASS')
        print('DURATION:', round(duration, 3))
        print('OUTPUT BYTES:', output_path.stat().st_size)
        print('STREAMING MUX TEST PASSED')

if __name__ == '__main__':
    main()
