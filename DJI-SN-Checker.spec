# -*- mode: python ; coding: utf-8 -*-
from importlib.metadata import distribution
from pathlib import Path
import sys

root = Path(SPECPATH)
datas = [
    (str(root / 'core.js'), '.'),
    (str(root / 'slider.js'), '.'),
    (str(root / 'tests/fixtures/device.html'), 'selftest'),
    (str(root / 'windows/使用说明.txt'), '.'),
    (str(root / 'third_party/slider-captcha-lab/LICENSE'), 'licenses/slider-captcha-lab'),
    (str(root / 'third_party/slider-captcha-lab/README.md'), 'licenses/slider-captcha-lab'),
    (str(root / 'third_party/tcl/license.terms'), 'licenses/tcl'),
    (str(Path(sys.base_prefix) / 'tcl/tk8.6/license.terms'), 'licenses/tk'),
]
for name in ('numpy', 'playwright', 'greenlet', 'pyee', 'typing_extensions', 'pyinstaller'):
    dist = distribution(name)
    for member in dist.files or []:
        if '.dist-info/' in str(member) and Path(str(member)).name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')):
            datas.append((str(dist.locate_file(member)), 'licenses/' + name))
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
datas.append((str(python_license), 'licenses/python'))

a = Analysis(
    [str(root / 'python_app.py')], pathex=[str(root)], binaries=[], datas=datas,
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='DJI-SN-Checker', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
    runtime_tmpdir=None, uac_admin=False, icon='NONE',
    version=str(root / 'windows/version_info.txt'),
)
