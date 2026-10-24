# -*- mode: python ; coding: utf-8 -*-
"""One-file windowed build of the D2R Save Sync window.

Run from the repository root, after `pip install -e ".[gui]" pyinstaller`:

    pyinstaller --noconfirm --clean D2RSaveSync.spec
"""

from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("d2rsync")
hidden += ["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"]

a = Analysis(
    ["d2rsync/gui_entry.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="D2RSaveSync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Windowed parent. Child processes (tasklist, and any other helper)
    # must pass CREATE_NO_WINDOW or the exe flashes a console on refresh.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)
