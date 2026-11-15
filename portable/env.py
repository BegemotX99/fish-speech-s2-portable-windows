"""Process-local paths; no registry, user profile, or system PATH changes."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def configure():
    paths = {
        'TEMP': 'temp', 'TMP': 'temp', 'TMPDIR': 'temp',
        'HF_HOME': 'cache/huggingface', 'HF_HUB_CACHE': 'cache/huggingface/hub',
        'HF_XET_CACHE': 'cache/huggingface/xet', 'TORCH_HOME': 'cache/torch',
        'TORCHINDUCTOR_CACHE_DIR': 'cache/inductor', 'TRITON_CACHE_DIR': 'cache/triton',
        'TORCH_EXTENSIONS_DIR': 'cache/torch-extensions', 'LIBROSA_CACHE_DIR': 'cache/librosa',
        'CUDA_CACHE_PATH': 'cache/cuda', 'NUMBA_CACHE_DIR': 'cache/numba',
        'MPLCONFIGDIR': 'cache/matplotlib', 'XDG_CACHE_HOME': 'cache',
        'TIKTOKEN_CACHE_DIR': 'cache/tiktoken', 'MODELSCOPE_CACHE': 'cache/modelscope',
        'UV_CACHE_DIR': 'cache/uv', 'PIP_CACHE_DIR': 'cache/pip',
        'IPYTHONDIR': 'cache/ipython',
    }
    for key, relative in paths.items():
        folder = ROOT / relative
        folder.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(folder)
    os.environ['PATH'] = str(ROOT / 'runtime/ffmpeg/bin') + os.pathsep + os.environ.get('PATH', '')
    os.environ['PYTHONNOUSERSITE'] = '1'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['DO_NOT_TRACK'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    for relative in ('logs', 'output', 'voices', 'checkpoints/s2-pro'):
        (ROOT / relative).mkdir(parents=True, exist_ok=True)

configure()
