import importlib
import json
import subprocess
import sys
from env import ROOT

def main():
    sys.path.insert(0, str(ROOT / 'app'))
    import torch
    import torchaudio
    print('Python:', sys.version)
    print('Portable root:', ROOT)
    print('PyTorch:', torch.__version__, 'CUDA runtime:', torch.version.cuda)
    print('Torchaudio:', torchaudio.__version__)
    if hasattr(torch._C, '_cuda_getArchFlags'):
        print('Compiled CUDA architectures:', torch._C._cuda_getArchFlags())
    for name in ('fish_speech.models.text2semantic.inference', 'fish_speech.models.dac.inference',
                 'fish_speech.inference_engine', 'fish_speech.utils.schema'):
        importlib.import_module(name)
        print('IMPORT OK:', name)
    subprocess.run([str(ROOT / 'runtime/ffmpeg/bin/ffmpeg.exe'), '-version'], check=True)
    manifest = json.loads((ROOT / 'portable/model-manifest.json').read_text('utf-8'))
    missing = [x['name'] for x in manifest['files'] if not (ROOT / 'checkpoints/s2-pro' / x['name']).is_file()]
    print('Model files:', 'present' if not missing else 'NOT DOWNLOADED: ' + ', '.join(missing))
    if torch.cuda.is_available():
        prop = torch.cuda.get_device_properties(0)
        print('GPU:', prop.name, 'VRAM GiB:', round(prop.total_memory / 2**30, 2))
        print('Capability:', torch.cuda.get_device_capability(0), 'Wheel architectures:', torch.cuda.get_arch_list())
        x = torch.randn(256, 256, device='cuda', dtype=torch.bfloat16)
        assert torch.isfinite(x @ x).all().item()
        torch.cuda.synchronize()
        print('CUDA BF16 MATRIX TEST: OK')
    else:
        print('CUDA NOT AVAILABLE. GPU synthesis cannot run on this computer.')
        if '--require-cuda' in sys.argv:
            raise SystemExit(2)

if __name__ == '__main__':
    main()
