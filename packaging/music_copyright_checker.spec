# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the music-copyright-checker binaries.

Builds a single self-contained executable per platform that serves both the
CLI and the local JSON server (see music_copyright_checker/entrypoints.py).
Run from the repository root:

    pyinstaller --noconfirm packaging/music_copyright_checker.spec

Artifact name: ``music-copyright-checker`` (+ ``.exe`` on Windows).
"""

from os.path import dirname, join

from PyInstaller.utils.hooks import collect_submodules

ROOT = dirname(SPECPATH)

hiddenimports = collect_submodules("opencode_harness") + collect_submodules(
    "spotapi", filter=lambda name: not name.startswith("spotapi._tests")
)

a = Analysis(
    [join(ROOT, "music_copyright_checker", "entrypoints.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "PyQt5", "tkinter", "spotapi._tests"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="music-copyright-checker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)