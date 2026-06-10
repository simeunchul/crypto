# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Crypto Bot 데스크톱 앱 (onedir 배포).

빌드:   pyinstaller crypto_bot.spec
결과:   dist/CryptoBot/CryptoBot.exe  (+ 동봉 리소스)

배포 시 dist/CryptoBot/ 폴더를 통째로 전달. 사용자는 그 폴더에 .env 를 넣고
CryptoBot.exe 실행. 로그/토큰은 같은 폴더의 data/ 에 생성된다.
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ("app/web", "app/web"),   # 웹 대시보드 정적 파일
]
binaries = []
hiddenimports = []

# pywebview (Windows: pythonnet/EdgeChromium 백엔드 포함)
for pkg in ("webview",):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

# uvicorn 은 loop/protocol 모듈을 동적 import → 명시 필요
hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "anyio", "anyio._backends._asyncio",
    "binance", "binance.client",
    "autotrader", "autotrader.live", "autotrader.live.engine",
    "autotrader.broker.binance_testnet_client",
]

block_cipher = None

a = Analysis(
    ["run_desktop.py"],
    pathex=["lib"],          # autotrader 패키지 탐색 경로
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "PyQt5", "PySide2", "tkinter", "pandas.tests"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CryptoBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,           # 콘솔 유지: 토큰/주소 출력 확인용. False 로 바꾸면 창만.
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
    upx_exclude=[],
    name="CryptoBot",
)
