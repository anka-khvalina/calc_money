# -*- mode: python ; coding: utf-8 -*-
"""Сборка Windows: pyinstaller scripts/FairOddsCalc.spec"""

from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH).resolve().parent
APP = ROOT / "app"

a = Analysis(
    [str(APP / "fair_odds_calc.py")],
    pathex=[str(APP)],
    binaries=[],
    datas=[
        (str(ROOT / "data" / "teams"), "data/teams"),
        (str(ROOT / "data" / "history"), "data/history"),
        (str(ROOT / "docs" / "examples"), "docs/examples"),
    ],
    hiddenimports=[
        "history_store",
        "team_registry",
        "team_ranking",
        "match_shin_calc",
        "devig_shin",
        "runtime_paths",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="FairOddsCalc",
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
