"""경로 해석 — 개발 실행과 PyInstaller frozen(.exe) 양쪽에서 동작.

frozen(onedir .exe):
  APP_ROOT   = exe 가 있는 폴더  → .env / data/ (사용자가 수정·기록하는 파일)
  BUNDLE_DIR = PyInstaller 번들 폴더(_MEIPASS) → web/ 등 읽기전용 리소스
개발:
  APP_ROOT = BUNDLE_DIR = 프로젝트 루트
"""

from __future__ import annotations

import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    APP_ROOT = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_ROOT))
else:
    APP_ROOT = Path(__file__).resolve().parent.parent
    BUNDLE_DIR = APP_ROOT

# 사용자 파일 (exe 옆, 수정 가능)
ENV_FILE = APP_ROOT / ".env"
DATA_DIR = APP_ROOT / "data"
TOKEN_FILE = DATA_DIR / ".api_token"

# 번들 리소스 (읽기 전용)
WEB_DIR = BUNDLE_DIR / "app" / "web"
LIB_DIR = BUNDLE_DIR / "lib"
