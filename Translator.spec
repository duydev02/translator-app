# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Translator app.
Build with:  pyinstaller Translator.spec
Outputs:     dist/Translator.exe
"""
from PyInstaller.utils.hooks import collect_all

# Bundle the icon at the root of the app (keeps sys._MEIPASS/image.ico lookup working).
datas = [('assets/image.ico', '.')]
binaries = []
hiddenimports = []

# Optional drag-and-drop support if tkinterdnd2 is installed.
try:
    tmp_ret = collect_all('tkinterdnd2')
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]
except Exception:
    pass


a = Analysis(
    ['translator.py'],
    pathex=[],
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
    name='Translator',
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
    version='assets/version.txt',
    icon=['assets/image.ico'],
)
