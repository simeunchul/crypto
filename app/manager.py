"""BotManager — 단일 봇 엔진의 생명주기(start/stop)와 read-only 조회를 관리.

서버 프로세스당 봇 1개만 (실거래 계정 1개 가정). 스레드로 엔진을 돌리고
graceful stop 을 보낸다. 봇이 꺼져 있을 때도 잔고/포지션 조회용 read-only
client 를 lazy 하게 만들어 제공.
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime

from .paths import DATA_DIR, LIB_DIR

sys.path.insert(0, str(LIB_DIR))

from autotrader.live import BotConfig, BotEngine  # noqa: E402


class BotManager:
    def __init__(self):
        self._engine: BotEngine | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._ro_client = None          # read-only 조회용 (idle 시 잔고)
        self._ro_env = None

    # ── env / client ──────────────────────────────────────────────
    def env(self) -> str:
        return os.environ.get("BINANCE_ENV", "testnet").lower()

    def _read_only_client(self):
        """잔고/포지션 조회용 client (캐시). 키 없으면 예외."""
        from autotrader.broker.binance_testnet_client import (
            BinanceTestnetClient, BinanceTestnetConfig,
        )
        cur_env = self.env()
        if self._ro_client is None or self._ro_env != cur_env:
            self._ro_client = BinanceTestnetClient(BinanceTestnetConfig.from_env())
            self._ro_env = cur_env
        return self._ro_client

    # ── lifecycle ─────────────────────────────────────────────────
    def is_running(self) -> bool:
        with self._lock:
            return self._engine is not None and self._engine.is_running

    def start(self, config: BotConfig, on_event=None) -> dict:
        with self._lock:
            if self._engine is not None and self._engine.is_running:
                return {"ok": False, "error": "이미 실행 중입니다. 먼저 정지하세요."}
            today = datetime.now().strftime("%Y%m%d_%H%M%S")
            if not config.log_path:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                config.log_path = str(DATA_DIR / f"crypto_app_log_{today}.json")
            self._engine = BotEngine(config, on_event=on_event)
            self._thread = threading.Thread(
                target=self._engine.run, name="bot-engine", daemon=True)
            self._thread.start()
            return {"ok": True, "status": self._engine.snapshot()}

    def stop(self) -> dict:
        with self._lock:
            if self._engine is None or not self._engine.is_running:
                return {"ok": False, "error": "실행 중인 봇이 없습니다."}
            self._engine.request_stop()
            return {"ok": True, "message": "정지 요청 전송 — graceful shutdown 중."}

    # ── queries ───────────────────────────────────────────────────
    def status(self) -> dict:
        with self._lock:
            if self._engine is None:
                return {"state": "idle", "env": self.env(), "config": None,
                        "positions": []}
            return self._engine.snapshot()

    def logs(self, n: int = 200) -> list[dict]:
        with self._lock:
            if self._engine is None:
                return []
            return self._engine.logs(n)

    def balance(self) -> dict:
        """live 잔고. 봇 실행 중이면 엔진 client 재사용 (rate-limit 절약은 추후)."""
        c = self._read_only_client()
        return c.balance()

    def positions(self) -> list[dict]:
        c = self._read_only_client()
        bal = c.balance()
        return bal.get("positions", [])
