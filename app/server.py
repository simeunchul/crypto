"""FastAPI 백엔드 — 봇 모니터링 + 시작/정지.

보안 모델:
  - 127.0.0.1 (localhost) 요청 → 무인증 허용 (데스크톱 앱이 같은 PC에서 접속)
  - 그 외 IP (폰 등 LAN) 요청 → Bearer 토큰 필수
  토큰은 CRYPTO_API_TOKEN env, 없으면 data/.api_token 에 생성/영속.

엔드포인트:
  GET  /api/health           무인증 ping
  GET  /api/status           엔진 스냅샷
  GET  /api/balance          live 잔고
  GET  /api/positions        live 포지션
  GET  /api/logs?n=200       최근 로그
  GET  /api/presets          실행 프리셋 목록
  POST /api/start            {preset|config} 로 봇 시작
  POST /api/stop             봇 정지
  WS   /ws                   status + log 실시간 푸시 (2s 주기)
  /                          웹 대시보드 (정적)
"""

from __future__ import annotations

import asyncio
import os
import secrets
from pathlib import Path

from fastapi import (
    FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect, Depends,
)
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .manager import BotManager
from . import presets as preset_mod
from .paths import ENV_FILE, DATA_DIR, TOKEN_FILE, WEB_DIR, LIB_DIR

import sys
sys.path.insert(0, str(LIB_DIR))
from autotrader.live import BotConfig  # noqa: E402


def load_env():
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


load_env()
manager = BotManager()


def resolve_token() -> str:
    tok = os.environ.get("CRYPTO_API_TOKEN", "").strip()
    if tok:
        return tok
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    tok = secrets.token_urlsafe(18)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(tok, encoding="utf-8")
    return tok


API_TOKEN = resolve_token()


def _is_loopback(host: str | None) -> bool:
    return host in ("127.0.0.1", "::1", "localhost", None)


def _lan_ip() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def require_auth(request: Request):
    """loopback 은 통과, 그 외엔 Bearer 토큰 검증."""
    client_host = request.client.host if request.client else None
    if _is_loopback(client_host):
        return
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else \
        request.headers.get("x-api-token", "").strip()
    if not token or not secrets.compare_digest(token, API_TOKEN):
        raise HTTPException(status_code=401, detail="invalid or missing token")


class StartRequest(BaseModel):
    preset: str | None = None
    config: dict | None = None


def create_app() -> FastAPI:
    app = FastAPI(title="Crypto Bot Control", version="1.0")

    @app.get("/api/health")
    def health():
        return {"ok": True, "env": manager.env(), "running": manager.is_running()}

    @app.get("/api/pairing")
    def pairing(request: Request):
        # 폰 앱 연결 정보 — loopback(데스크톱 앱)에서만 노출
        client_host = request.client.host if request.client else None
        if not _is_loopback(client_host):
            raise HTTPException(403, "pairing info is local-only")
        port = int(os.environ.get("CRYPTO_SERVER_PORT", "8787"))
        lan_ip = _lan_ip()
        return {
            "lan_ip": lan_ip,
            "port": port,
            "url": f"http://{lan_ip}:{port}",
            "token": API_TOKEN,
            "env": manager.env(),
        }

    @app.get("/api/status", dependencies=[Depends(require_auth)])
    def status():
        return manager.status()

    @app.get("/api/balance", dependencies=[Depends(require_auth)])
    def balance():
        try:
            return {"ok": True, "balance": manager.balance()}
        except Exception as e:
            return JSONResponse(status_code=502,
                                content={"ok": False, "error": str(e)[:200]})

    @app.get("/api/positions", dependencies=[Depends(require_auth)])
    def positions():
        try:
            return {"ok": True, "positions": manager.positions()}
        except Exception as e:
            return JSONResponse(status_code=502,
                                content={"ok": False, "error": str(e)[:200]})

    @app.get("/api/logs", dependencies=[Depends(require_auth)])
    def logs(n: int = 200):
        return {"ok": True, "logs": manager.logs(n)}

    @app.get("/api/presets", dependencies=[Depends(require_auth)])
    def presets():
        return {"ok": True, "presets": preset_mod.list_presets(),
                "default": preset_mod.DEFAULT_PRESET}

    @app.post("/api/start", dependencies=[Depends(require_auth)])
    def start(req: StartRequest):
        if req.preset:
            cfg_dict = preset_mod.get_preset_config(req.preset)
            if cfg_dict is None:
                raise HTTPException(400, f"unknown preset: {req.preset}")
        elif req.config:
            cfg_dict = req.config
        else:
            raise HTTPException(400, "preset 또는 config 필요")
        try:
            cfg = BotConfig(**{k: v for k, v in cfg_dict.items()
                               if k in BotConfig.__dataclass_fields__})
        except Exception as e:
            raise HTTPException(400, f"config 오류: {e}")
        result = manager.start(cfg)
        if not result.get("ok"):
            raise HTTPException(409, result.get("error", "start fail"))
        return result

    @app.post("/api/stop", dependencies=[Depends(require_auth)])
    def stop():
        result = manager.stop()
        if not result.get("ok"):
            raise HTTPException(409, result.get("error", "stop fail"))
        return result

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        # WS 인증: loopback 통과, 그 외 ?token= 검증
        client_host = websocket.client.host if websocket.client else None
        if not _is_loopback(client_host):
            token = websocket.query_params.get("token", "")
            if not token or not secrets.compare_digest(token, API_TOKEN):
                await websocket.close(code=4401)
                return
        await websocket.accept()
        last_log_count = 0
        try:
            while True:
                snap = manager.status()
                all_logs = manager.logs(800)
                new_logs = all_logs[last_log_count:] if last_log_count < len(all_logs) else []
                last_log_count = len(all_logs)
                await websocket.send_json({
                    "type": "tick",
                    "status": snap,
                    "logs": new_logs,
                })
                await asyncio.sleep(2.0)
        except WebSocketDisconnect:
            return
        except Exception:
            return

    # ── static web UI ─────────────────────────────────────────────
    @app.get("/")
    def index():
        idx = WEB_DIR / "index.html"
        if idx.exists():
            return FileResponse(str(idx))
        return JSONResponse({"detail": "web UI not built"}, status_code=404)

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    return app


app = create_app()


def run_server(host: str = "127.0.0.1", port: int = 8787):
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 으로 두면 폰 등 LAN 에서 접속 가능 (토큰 필요)")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    os.environ["CRYPTO_SERVER_PORT"] = str(args.port)
    print(f"[server] API token: {API_TOKEN}")
    print(f"[server] http://{args.host}:{args.port}  (env={manager.env()})")
    run_server(args.host, args.port)
