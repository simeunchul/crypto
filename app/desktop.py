"""PC 데스크톱 앱 진입점.

FastAPI 서버를 백그라운드 스레드로 띄우고, 네이티브 창(pywebview)으로 대시보드를
연다. 서버는 0.0.0.0 바인딩 → 같은 와이파이의 폰 앱도 접속 가능(토큰 필요).
창은 127.0.0.1 로 열어 로컬은 무인증.

PyInstaller 로 단일 .exe 패키징 가능 (build_exe.py 참고).
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request

# Windows 콘솔(cp949) 에서 한글/em-dash 출력 시 크래시 방지
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# .env 로드 + manager/token 준비 (server import 시 수행됨)
from app import server as srv


def _free_port(preferred: int = 8787) -> int:
    for port in (preferred, 8788, 8789, 0):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
            p = s.getsockname()[1]
            s.close()
            return p
        except OSError:
            continue
    return preferred


def _wait_until_up(url: str, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def main():
    port = _free_port(int(os.environ.get("CRYPTO_SERVER_PORT", "8787")))
    os.environ["CRYPTO_SERVER_PORT"] = str(port)

    env = srv.manager.env()
    lan = srv._lan_ip()
    print("=" * 56)
    print(f"  Crypto Bot — {env.upper()}")
    print(f"  로컬:  http://127.0.0.1:{port}")
    print(f"  폰 앱: http://{lan}:{port}   (토큰 필요)")
    print(f"  토큰:  {srv.API_TOKEN}")
    print("=" * 56)

    # 서버를 0.0.0.0 으로 (폰 접속 허용). 로컬 창은 127.0.0.1 로 연다.
    t = threading.Thread(
        target=srv.run_server, kwargs={"host": "0.0.0.0", "port": port},
        daemon=True)
    t.start()

    local_url = f"http://127.0.0.1:{port}/"
    if not _wait_until_up(local_url + "api/health"):
        print("[desktop] 서버 시작 실패")
        return

    title = f"Crypto Bot — {'실거래 MAINNET' if env == 'mainnet' else 'TESTNET'}"
    try:
        import webview
        webview.create_window(title, local_url, width=1040, height=820,
                              min_size=(420, 600))
        webview.start()
    except Exception as e:
        print(f"[desktop] pywebview 사용 불가 ({e}). 기본 브라우저로 엽니다.")
        import webbrowser
        webbrowser.open(local_url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
