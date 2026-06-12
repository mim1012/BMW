# -*- mode: python ; coding: utf-8 -*-
import os, glob
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.dirname(os.path.abspath(SPEC))

def src(filename):
    return (os.path.join(ROOT, filename), '.')

datas = []
if os.path.exists(os.path.join(ROOT, 'scanned_models.json')):
    datas.append(src('scanned_models.json'))
if os.path.exists(os.path.join(ROOT, 'form_fields.json')):
    datas.append(src('form_fields.json'))
for p in sorted(glob.glob(os.path.join(ROOT, 'form_fields_*.json'))):
    datas.append((p, '.'))

binaries = []
hiddenimports = []
for pkg in ('playwright', 'greenlet', 'pyee'):
    tmp = collect_all(pkg)
    datas         += tmp[0]
    binaries      += tmp[1]
    hiddenimports += tmp[2]

a = Analysis(
    [os.path.join(ROOT, 'gui_app.py')],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BMW-AutoBuyer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
