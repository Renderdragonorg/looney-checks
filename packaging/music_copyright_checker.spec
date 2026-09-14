# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the music-copyright-checker binaries.

Builds a single self-contained **onedir** distribution per platform that serves
both the CLI and the local JSON server (see music_copyright_checker/entrypoints.py).
CI packages the directory as ``.tar.gz`` (Linux/macOS) or ``.zip`` (Windows).

Why onedir and not onefile: a onefile binary re-extracts the whole bundle to a
temp directory on *every* launch, and macOS then re-scans each extracted file.
That made warm startup ~17s locally (and much worse with Gatekeeper). onedir
starts in well under a second once unpacked. See docs/binaries.md.

Run from the repository root:

    pyinstaller --noconfirm --distpath dist packaging/music_copyright_checker.spec

Artifact: ``dist/music-copyright-checker/music-copyright-checker`` (+ ``.exe``
on Windows).
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
    [],
    exclude_binaries=True,
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="music-copyright-checker",
)
