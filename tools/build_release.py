"""Rebuild the small installer ZIP; never package local models, logs, or tokens."""
import hashlib
from pathlib import Path
import sys
import zipfile

root = Path(__file__).resolve().parent.parent
dest = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root.parent / 'FishSpeech-S2-Portable-Windows-v1.0.0.zip'
files = [root / name for name in (
    '1_INSTALL_AND_RUN.bat', '2_RUN.bat', '3_DIAGNOSTICS.bat', '4_VERIFY_MODEL.bat',
    'settings.json', 'requirements.lock', 'README.md', 'MANUAL_RU.html',
    'VALIDATION.md', 'LICENSE', 'MODEL-LICENSE.md', 'NOTICE', '.gitignore', '.gitattributes',
)]
for directory in ('portable', 'tools'):
    files += [p for p in (root / directory).rglob('*') if p.is_file()
              and '__pycache__' not in p.parts and p.suffix != '.pyc']
dest.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(dest, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(files):
        info = zipfile.ZipInfo('FishSpeech-S2-Portable/' + path.relative_to(root).as_posix(), (2026, 11, 16, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, path.read_bytes())
digest = hashlib.sha256(dest.read_bytes()).hexdigest()
dest.with_name(dest.name + '.sha256').write_text(f'{digest}  {dest.name}\n', encoding='ascii')
print(f'{dest.name}: {dest.stat().st_size:,} bytes; SHA256 {digest}')
