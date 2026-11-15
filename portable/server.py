"""Small local Russian UI around the upstream S2 inference engine."""
import argparse
import base64
import datetime
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from env import ROOT

sys.path.insert(0, str(ROOT / 'app'))
TOKEN = secrets.token_urlsafe(32)
STATE = {'state': 'loading', 'message': 'Загрузка модели. Следите за консолью.'}
LOCK = threading.Lock()
JOBS = {}
ENGINE = None
ARGS = None

def split_text(text, limit=220):
    """Independent short requests avoid accumulating KV history on a 16 GB GPU."""
    parts, current = [], ''
    for sentence in re.split(r'(?<=[.!?…])\s+|\n+', text.strip()):
        for word in sentence.split():
            if len(word) > limit:
                raise ValueError('Слишком длинное слово. Разделите текст пробелами.')
            if current and len(current) + len(word) + 1 > limit:
                parts.append(current)
                current = ''
            current = (current + ' ' + word).strip()
        if len(current) >= limit // 2:
            parts.append(current)
            current = ''
    if current:
        parts.append(current)
    return parts

def load_engine():
    global ENGINE
    try:
        import torch
        if ARGS.device == 'cpu':
            torch.set_num_threads(min(8, os.cpu_count() or 1))
        if ARGS.device == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError('CUDA недоступна. Запустите 3_DIAGNOSTICS.bat и проверьте драйвер NVIDIA.')
        from memory import configure_model
        configure_model(ARGS.context)
        from fish_speech.models.text2semantic.inference import launch_thread_safe_queue
        from fish_speech.models.dac.inference import load_model
        from fish_speech.inference_engine import TTSInferenceEngine
        precision = torch.bfloat16 if ARGS.device == 'cuda' else torch.float32
        if ARGS.device == 'cuda':
            print('GPU:', torch.cuda.get_device_name(0), flush=True)
        queue = launch_thread_safe_queue(str(ROOT / 'checkpoints/s2-pro'), ARGS.device, precision, compile=False)
        # CPU checkpoint load avoids a second full-precision copy in GPU memory.
        decoder = load_model('modded_dac_vq', str(ROOT / 'checkpoints/s2-pro/codec.pth'), device='cpu')
        decoder = decoder.to(device=ARGS.device)  # Keep codec FP32 as in upstream; autocast during inference.
        ENGINE = TTSInferenceEngine(queue, decoder, precision, compile=False, model_family='s2')
        STATE.update(state='ready', message='Модель загружена. Можно создавать аудио.')
    except Exception as exc:
        traceback.print_exc()
        STATE.update(state='error', message=str(exc))

def generate(job_id, payload):
    import numpy as np
    import soundfile as sf
    from fish_speech.utils.schema import ServeTTSRequest, ServeReferenceAudio
    job = JOBS[job_id]
    try:
        text = str(payload.get('text', '')).strip()
        if not text or len(text) > 10000:
            raise ValueError('Введите от 1 до 10000 символов.')
        # This compact UI supports one speaker per job. Avoid corrupting multi-speaker tags when splitting.
        if '<|speaker:' in text:
            raise ValueError('Этот интерфейс поддерживает одного диктора. Уберите теги <|speaker:N|>.')
        chunks = split_text(text)
        temperature = float(payload.get('temperature', 0.7))
        seed_text = str(payload.get('seed', '')).strip()
        seed = int(seed_text) if seed_text else None
        if not 0.1 <= temperature <= 1.0:
            raise ValueError('Температура должна быть от 0.1 до 1.0.')
        samples, sample_rate = [], None
        with tempfile.TemporaryDirectory(prefix='reference-', dir=ROOT / 'temp') as tmp:
            refs = []
            if payload.get('audio'):
                transcript = str(payload.get('reference_text', '')).strip()
                if not transcript or len(transcript) > 1500:
                    raise ValueError('Нужна точная расшифровка образца голоса (до 1500 символов).')
                raw = base64.b64decode(payload['audio'], validate=True)
                if len(raw) > 20 * 1024**2:
                    raise ValueError('Образец должен быть меньше 20 МБ.')
                src, wav = Path(tmp) / 'input.audio', Path(tmp) / 'reference.wav'
                src.write_bytes(raw)
                ffmpeg = ROOT / 'runtime/ffmpeg/bin/ffmpeg.exe'
                subprocess.run([str(ffmpeg), '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                                '-i', str(src), '-t', '20', '-vn', '-ac', '1', '-ar', '44100', str(wav)],
                               check=True, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                info = sf.info(wav)
                if info.duration < 2:
                    raise ValueError('Образец слишком короткий: нужно от 2 до 20 секунд.')
                refs = [ServeReferenceAudio(audio=wav.read_bytes(), text=transcript, speaker=0)]
            for i, chunk in enumerate(chunks):
                job.update(message=f'Фрагмент {i + 1} из {len(chunks)}', progress=(i * 100 // len(chunks)))
                req = ServeTTSRequest(text=chunk, references=refs, format='wav', streaming=False,
                                      chunk_length=200, max_new_tokens=1024, temperature=temperature,
                                      top_p=0.7, top_k=30, seed=seed if i == 0 else None, use_memory_cache='on')
                final = None
                for result in ENGINE.inference(req):
                    if result.code == 'error':
                        raise RuntimeError(result.error)
                    if result.code == 'final':
                        sample_rate, final = result.audio
                if final is None or not len(final) or not np.isfinite(final).all():
                    raise RuntimeError('Модель не вернула корректное аудио. Подробности в logs.')
                samples.append(final)
                if i + 1 < len(chunks):
                    samples.append(np.zeros(int(sample_rate * 0.12), dtype=np.float32))
            basename = datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + job_id[:8]
            out = ROOT / 'output' / (basename + '.wav')
            sf.write(out, np.concatenate(samples), sample_rate, subtype='PCM_16')
            (ROOT / 'output' / (basename + '.txt')).write_text(text, encoding='utf-8')
            job.update(state='done', progress=100, message='Готово. WAV сохранён в output.', url='/output/' + out.name)
    except Exception as exc:
        traceback.print_exc()
        job.update(state='error', message=str(exc))
    finally:
        if ENGINE is not None:
            ENGINE.ref_by_hash.clear()
            ENGINE.ref_by_id.clear()
        LOCK.release()

class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, mime='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/':
            self.send(200, (ROOT / 'portable/ui.html').read_text('utf-8').replace('__TOKEN__', TOKEN), 'text/html; charset=utf-8')
        elif self.path == '/health':
            self.send(200, STATE)
        elif self.path.startswith('/jobs/'):
            self.send(200, JOBS.get(self.path[6:], {'state': 'error', 'message': 'Задание не найдено.'}))
        elif re.fullmatch(r'/output/[0-9]{8}-[0-9]{6}-[a-f0-9]{8}\.wav', self.path):
            file = ROOT / 'output' / self.path.rsplit('/', 1)[1]
            if file.is_file():
                self.send(200, file.read_bytes(), 'audio/wav')
            else:
                self.send(404, {'error': 'File not found'})
        else:
            self.send(404, {'error': 'Not found'})

    def do_POST(self):
        if self.headers.get('X-Fish-Token') != TOKEN:
            self.send(403, {'error': 'Invalid session token'})
            return
        if self.path != '/generate':
            self.send(404, {'error': 'Not found'})
            return
        if STATE['state'] != 'ready':
            self.send(503, {'error': STATE['message']})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 29 * 1024**2:
                raise ValueError('Размер запроса превышен (максимум 20 МБ аудио).')
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError('Invalid JSON object')
            if not LOCK.acquire(blocking=False):
                self.send(409, {'error': 'Дождитесь завершения текущего задания.'})
                return
            job_id = secrets.token_hex(16)
            # Retain only recent statuses; WAV files remain available in output.
            if len(JOBS) >= 100:
                JOBS.pop(next(iter(JOBS)))
            JOBS[job_id] = {'state': 'running', 'progress': 0, 'message': 'Подготовка...'}
            threading.Thread(target=generate, args=(job_id, payload), daemon=True).start()
            self.send(202, {'id': job_id})
        except (ValueError, TypeError) as exc:
            self.send(400, {'error': str(exc)})

def main():
    global ARGS
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=7860)
    parser.add_argument('--context', type=int, choices=(4096, 8192), default=4096)
    parser.add_argument('--device', choices=('cuda', 'cpu'), default='cuda')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--ui-test', action='store_true', help='UI only; no model loaded')
    ARGS = parser.parse_args()
    os.chdir(ROOT / 'app')
    # Binding first detects port conflicts before loading 11 GB of model weights.
    server = ThreadingHTTPServer(('127.0.0.1', ARGS.port), Handler)
    server.daemon_threads = True
    print(f'Built with Fish Audio. Open http://127.0.0.1:{ARGS.port}', flush=True)
    if ARGS.ui_test:
        STATE.update(state='error', message='Режим проверки интерфейса: модель не загружена.')
    else:
        threading.Thread(target=load_engine, daemon=True).start()
    if not ARGS.no_browser:
        webbrowser.open(f'http://127.0.0.1:{ARGS.port}')
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print('Stopped.', flush=True)
    finally:
        server.server_close()

if __name__ == '__main__':
    from loguru import logger
    logger.add(str(ROOT / 'logs/inference-{time:YYYY-MM-DD}.log'), rotation='10 MB', retention=5, encoding='utf-8')
    try:
        main()
    except Exception:
        logger.exception('Startup failed')
        raise
