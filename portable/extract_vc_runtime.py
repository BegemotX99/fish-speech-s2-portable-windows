"""Extract signed Microsoft redist's x64 DLLs app-locally; never execute MSI/EXE."""
import hashlib
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from env import ROOT

def expand(cab, dest):
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(['expand.exe', '-F:*', str(cab), str(dest)], check=True,
                   stdout=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

def main():
    exe = Path(sys.argv[1])
    data = exe.read_bytes()
    if hashlib.sha256(data).hexdigest() != 'cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b':
        raise RuntimeError('Unexpected Visual C++ Runtime installer hash')
    with tempfile.TemporaryDirectory(prefix='vcruntime-', dir=ROOT / 'temp') as tmp:
        tmp = Path(tmp)
        containers = []
        pos = 0
        while (pos := data.find(b'MSCF\0\0\0\0', pos)) >= 0:
            size = struct.unpack_from('<I', data, pos + 8)[0]
            if 36 <= size <= len(data) - pos and data[pos + 24:pos + 26] == b'\x03\x01':
                cab = tmp / f'container-{len(containers)}.cab'
                cab.write_bytes(data[pos:pos + size])
                containers.append(cab)
                pos += size
            else:
                pos += 8
        if len(containers) != 2:
            raise RuntimeError('Unexpected redist container format')
        meta, payloads, dlls = tmp / 'meta', tmp / 'payloads', tmp / 'dlls'
        expand(containers[0], meta)
        expand(containers[1], payloads)
        manifest = ET.parse(meta / '0').getroot()
        ns = {'b': 'http://schemas.microsoft.com/wix/2008/Burn'}
        entry = next(x for x in manifest.findall('b:Payload', ns)
                     if x.get('FilePath') == r'packages\vcRuntimeMinimum_amd64\cab1.cab')
        cabinet = payloads / entry.get('SourcePath')
        if hashlib.sha1(cabinet.read_bytes()).hexdigest().upper() != entry.get('Hash'):
            raise RuntimeError('Redist cabinet payload checksum mismatch')
        expand(cabinet, dlls)
        py_dir = Path(sys.executable).parent
        count = 0
        for file in dlls.iterdir():
            if file.name.endswith('.dll_amd64'):
                target = py_dir / file.name.removesuffix('_amd64')
                # CPython already ships these two loaded DLLs. Windows locks them
                # while this extraction script runs; retain the supplied copies.
                retain = target.is_file() and (
                    target.name in ('vcruntime140.dll', 'vcruntime140_1.dll')
                    or hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(file.read_bytes()).digest()
                )
                if not retain:
                    shutil.copy2(file, target)
                count += 1
        if not (py_dir / 'msvcp140.dll').is_file() or count < 10:
            raise RuntimeError('Visual C++ Runtime extraction incomplete')
        license_entry = next(x for x in manifest.findall('b:UX/b:Payload', ns)
                             if x.get('FilePath') == 'license.rtf')
        shutil.copy2(meta / license_entry.get('SourcePath'), py_dir / 'VC-RUNTIME-LICENSE.rtf')
        (py_dir / '.vc-runtime-ready').write_text('14.44-local-x64', encoding='ascii')
        print(f'Visual C++ Runtime: {count} local DLLs installed beside portable python.exe.')

if __name__ == '__main__':
    main()
