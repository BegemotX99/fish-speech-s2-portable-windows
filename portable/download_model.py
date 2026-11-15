"""Pinned model download, resumable per file, SHA-256 verified before use."""
import concurrent.futures
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from env import ROOT

MANIFEST = ROOT / 'portable/model-manifest.json'

def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as src:
        for chunk in iter(lambda: src.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def fetch(entry, revision):
    name = entry['name']
    target = ROOT / 'checkpoints/s2-pro' / name
    if target.is_file() and target.stat().st_size == entry['size'] and sha256(target) == entry['sha256']:
        print(f'[OK] {name}', flush=True)
        return
    partial = target.with_name(target.name + '.part')
    url = f'https://huggingface.co/fishaudio/s2-pro/resolve/{revision}/{name}?download=true'
    for attempt in range(1, 6):
        try:
            offset = partial.stat().st_size if partial.exists() else 0
            if offset >= entry['size']:
                if offset == entry['size'] and sha256(partial) == entry['sha256']:
                    os.replace(partial, target)
                    return
                partial.unlink()
                offset = 0
            headers = {'User-Agent': 'FishSpeech-S2-Portable/1.0'}
            if offset:
                headers['Range'] = f'bytes={offset}-'
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90) as res:
                if offset and res.status == 206:
                    if not res.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                        raise RuntimeError('Invalid Content-Range')
                    mode = 'ab'
                else:
                    mode, offset = 'wb', 0
                print(f'[DOWNLOAD] {name}: {offset:,} / {entry["size"]:,}', flush=True)
                last = time.monotonic()
                with open(partial, mode) as out:
                    while chunk := res.read(4 * 1024 * 1024):
                        out.write(chunk)
                        offset += len(chunk)
                        if time.monotonic() - last > 20:
                            print(f'  {name}: {offset * 100 / entry["size"]:.1f}%', flush=True)
                            last = time.monotonic()
            if partial.stat().st_size < entry['size']:
                raise OSError(f'Incomplete download: {partial.stat().st_size} / {entry["size"]}; retaining partial file')
            if partial.stat().st_size != entry['size'] or sha256(partial) != entry['sha256']:
                partial.unlink(missing_ok=True)
                raise RuntimeError(f'Size or SHA-256 mismatch: {name}')
            os.replace(partial, target)
            print(f'[VERIFIED] {name}', flush=True)
            return
        except (OSError, RuntimeError) as exc:
            print(f'[RETRY {attempt}/5] {name}: {exc}', flush=True)
            if attempt == 5:
                raise
            time.sleep(min(attempt * 2, 10))

def main():
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fetch, entry, manifest['revision']) for entry in manifest['files']]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    (ROOT / 'checkpoints/s2-pro/.complete').write_text(manifest['revision'], encoding='ascii')
    print('S2-Pro download complete. All model files verified.', flush=True)

if __name__ == '__main__':
    main()
