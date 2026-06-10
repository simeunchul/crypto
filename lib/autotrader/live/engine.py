"""Thread-controllable live trading engine.

run_crypto_testnet.py 의 검증된 루프 로직을 그대로 옮겨, CLI / FastAPI 서버 /
데스크톱 앱이 동일한 엔진을 재사용하도록 한다.

핵심 차이: signal 핸들러 대신 threading.Event(stop) 로 graceful 종료를 받고,
매 tick 스냅샷(status)과 로그 버퍼를 thread-safe 하게 노출한다 → 서버가 폴링/푸시.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("crypto-engine")


# ── Binance USDT-M futures qty precision (LOT_SIZE filter 기준)
# 새 종목 추가 시 여기에 추가. 미등록 종목은 default 3 으로 fallback.
SYMBOL_QTY_PRECISION = {
    "BTCUSDT":   3,    # step 0.001
    "ETHUSDT":   3,    # step 0.001
    "BNBUSDT":   2,    # step 0.01
    "SOLUSDT":   2,    # step 0.01
    "AVAXUSDT":  0,    # 정수
    "DOGEUSDT":  0,
    "ADAUSDT":   0,
    "XRPUSDT":   1,
    "MATICUSDT": 0,
    "DOTUSDT":   1,
    "LINKUSDT":  2,
    "LTCUSDT":   3,
    "BCHUSDT":   3,
    "TRXUSDT":   0,
    "ARBUSDT":   1,
    "OPUSDT":    1,
    "SUIUSDT":   1,
    "INJUSDT":   1,
    "NEARUSDT":  0,
    "ATOMUSDT":  2,
}

# 봉 간격 (ms) — closed-bar 판정용.
INTERVAL_MS = {
    "1m":   60_000,
    "3m":   180_000,
    "5m":   300_000,
    "15m":  900_000,
    "30m":  1_800_000,
    "1h":   3_600_000,
    "2h":   7_200_000,
    "4h":   14_400_000,
    "6h":   21_600_000,
    "12h":  43_200_000,
    "1d":   86_400_000,
}


def round_qty(symbol: str, qty_raw: float) -> float:
    """종목별 LOT_SIZE filter 에 맞춰 qty 반올림. 미등록 종목은 precision=3 fallback."""
    p = SYMBOL_QTY_PRECISION.get(symbol, 3)
    q = round(qty_raw, p)
    if p == 0:
        q = int(q)
    return q


def compute_signal(klines: list, fast: int, slow: int) -> tuple[float, float, int]:
    closes = [float(k[4]) for k in klines]
    if len(closes) < slow:
        return 0, 0, 0
    s_fast = sum(closes[-fast:]) / fast
    s_slow = sum(closes[-slow:]) / slow
    sig = 1 if s_fast > s_slow else (-1 if s_fast < s_slow else 0)
    return s_fast, s_slow, sig


@dataclass
class SymbolState:
    symbol: str
    weight: float                # 0.0 ~ 1.0 (자본 분할 비중)
    current_position: int = 0    # +1 / -1 / 0
    last_action_tick: int = -1
    entry_price: Optional[float] = None
    high_water: Optional[float] = None
    low_water: Optional[float] = None
    stopped_until_signal_change: int = 0
    last_evaluated_bar_ts: int = 0
    # cooldown 정책용 — trail-stop 시점 봉의 open_time (ms). 0 = 차단 없음.
    stopped_at_bar_ts: int = 0
    # UI 표시용 최신 스냅샷
    mark_price: float = 0.0
    fast_ma: float = 0.0
    slow_ma: float = 0.0
    target_signal: int = 0
    last_action: str = "HOLD"
    # 주문 실패/재시도 추적 (state desync 방지)
    close_retry_count: int = 0
    last_order_error: Optional[str] = None


@dataclass
class BotConfig:
    """봇 실행 파라미터. CLI args / API body / preset 어디서든 동일하게 구성."""
    symbols: list[str]
    weights: Optional[list[float]] = None   # None = 균등 분할
    mode: str = "swing"                      # "swing" | "intraday"
    exit_rule: Optional[str] = None          # None → mode 로부터 유도
    trail_pct: float = 0.02
    interval: str = "4h"
    fast: int = 12
    slow: int = 48
    allow_short: bool = False
    leverage: int = 1
    position_pct: float = 0.95
    poll_min: float = 5.0
    duration_hours: float = 0.0              # 0 = 무한
    log_path: Optional[str] = None
    # 차단 정책 (backtest 검증: cooldown_1봉 = Sharpe +6.5 개선)
    block_policy: str = "signal_change"      # "signal_change" | "cooldown"
    cooldown_bars: int = 0                   # cooldown 일 때 차단 봉 수 (4h 단위)

    def resolved_exit_rule(self) -> str:
        if self.exit_rule:
            return self.exit_rule
        return "trail" if self.mode == "swing" else "flip"

    def normalized_weights(self) -> list[float]:
        if self.weights:
            w = list(self.weights)
            if len(w) != len(self.symbols):
                raise ValueError(
                    f"weights len ({len(w)}) != symbols len ({len(self.symbols)})")
        else:
            w = [1.0 / len(self.symbols)] * len(self.symbols)
        total = sum(w)
        return [x / total for x in w]

    def to_dict(self) -> dict:
        return {
            "symbols": self.symbols,
            "weights": self.normalized_weights(),
            "mode": self.mode,
            "exit_rule": self.resolved_exit_rule(),
            "trail_pct": self.trail_pct,
            "interval": self.interval,
            "fast": self.fast,
            "slow": self.slow,
            "allow_short": self.allow_short,
            "leverage": self.leverage,
            "position_pct": self.position_pct,
            "poll_min": self.poll_min,
            "duration_hours": self.duration_hours,
            "block_policy": self.block_policy,
            "cooldown_bars": self.cooldown_bars,
        }


@dataclass
class BotStatus:
    state: str = "idle"            # idle|starting|running|stopping|stopped|error
    env: str = "testnet"
    mode: str = "swing"
    exit_rule: str = "trail"
    started_at: Optional[str] = None
    uptime_sec: float = 0.0
    tick: int = 0
    starting_wallet: Optional[float] = None
    wallet_balance: Optional[float] = None
    margin_balance: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    error: Optional[str] = None
    symbols: list = field(default_factory=list)
    positions: list = field(default_factory=list)


class BotEngine:
    """검증된 MA-crossover swing/intraday 루프를 thread-safe 하게 감싼 엔진.

    server: engine = BotEngine(cfg); Thread(target=engine.run).start()
            engine.request_stop()  # graceful
            engine.snapshot()      # 현재 status dict
    """

    def __init__(
        self,
        config: BotConfig,
        client=None,
        on_event: Optional[Callable[[dict], None]] = None,
        log_buffer_size: int = 800,
    ):
        self.config = config
        self._client = client
        self._on_event = on_event
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._log_buffer: deque = deque(maxlen=log_buffer_size)
        self._status = BotStatus(
            mode=config.mode,
            exit_rule=config.resolved_exit_rule(),
        )
        self._states: dict[str, SymbolState] = {}
        self._started_monotonic: Optional[float] = None
        self._log_records: list[dict] = []
        self._log_path: Optional[Path] = None

    # ── public thread-safe accessors ──────────────────────────────
    def request_stop(self):
        self._stop.set()

    @property
    def is_running(self) -> bool:
        return self._status.state in ("starting", "running")

    def snapshot(self) -> dict:
        with self._lock:
            s = self._status
            if self._started_monotonic and s.state in ("running", "starting", "stopping"):
                s.uptime_sec = time.monotonic() - self._started_monotonic
            positions = []
            for st in self._states.values():
                positions.append({
                    "symbol": st.symbol,
                    "weight": st.weight,
                    "position": st.current_position,
                    "side": ("LONG" if st.current_position == 1
                             else "SHORT" if st.current_position == -1 else "FLAT"),
                    "entry_price": st.entry_price,
                    "mark_price": st.mark_price,
                    "high_water": st.high_water,
                    "low_water": st.low_water,
                    "fast_ma": st.fast_ma,
                    "slow_ma": st.slow_ma,
                    "target_signal": st.target_signal,
                    "last_action": st.last_action,
                })
            return {
                "state": s.state,
                "env": s.env,
                "mode": s.mode,
                "exit_rule": s.exit_rule,
                "started_at": s.started_at,
                "uptime_sec": round(s.uptime_sec, 1),
                "tick": s.tick,
                "starting_wallet": s.starting_wallet,
                "wallet_balance": s.wallet_balance,
                "margin_balance": s.margin_balance,
                "unrealized_pnl": s.unrealized_pnl,
                "pnl": s.pnl,
                "pnl_pct": s.pnl_pct,
                "error": s.error,
                "config": self.config.to_dict(),
                "positions": positions,
            }

    def logs(self, n: int = 200) -> list[dict]:
        with self._lock:
            return list(self._log_buffer)[-n:]

    # ── internal helpers ──────────────────────────────────────────
    def _log(self, level: str, msg: str):
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "msg": msg,
        }
        with self._lock:
            self._log_buffer.append(rec)
        getattr(logger, level if level in ("info", "warning", "error") else "info")(msg)
        self._emit({"type": "log", "data": rec})

    def _emit(self, event: dict):
        if self._on_event:
            try:
                self._on_event(event)
            except Exception:
                pass

    def _flush_records(self):
        if not self._log_path:
            return
        try:
            tmp = self._log_path.with_suffix(self._log_path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(self._log_records, indent=2, ensure_ascii=False),
                encoding="utf-8")
            tmp.replace(self._log_path)
        except Exception as e:
            logger.warning(f"log flush fail: {e}")

    # ── 엔진 상태 영속화 (재시작 시 trail 누적 데이터 보존) ──────────
    def _state_file_path(self) -> Path:
        """engine_state.json 경로. 로그 디렉토리와 동일 위치 사용."""
        if self._log_path:
            return self._log_path.parent / "engine_state.json"
        return Path("data") / "engine_state.json"

    def _save_state(self):
        """매 tick 후 호출. 모든 종목의 trail tracking 필드를 atomic 하게 저장."""
        try:
            path = self._state_file_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = {
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "symbols": {
                        sym: {
                            "current_position": st.current_position,
                            "entry_price": st.entry_price,
                            "high_water": st.high_water,
                            "low_water": st.low_water,
                            "stopped_until_signal_change": st.stopped_until_signal_change,
                            "stopped_at_bar_ts": st.stopped_at_bar_ts,
                            "last_evaluated_bar_ts": st.last_evaluated_bar_ts,
                            "close_retry_count": st.close_retry_count,
                        }
                        for sym, st in self._states.items()
                    },
                }
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8")
            tmp.replace(path)
        except Exception as e:
            logger.warning(f"engine state save fail: {e}")

    def _load_state(self) -> dict | None:
        """run() 시작 시 호출. 직전 봇 종료 시점의 trail tracking 필드 복원.

        반환: {symbol: {field: value}}  또는 None (파일 없음/손상)
        """
        try:
            path = self._state_file_path()
            if not path.exists():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            saved_at = data.get("saved_at", "?")
            symbols = data.get("symbols", {})
            self._log("info",
                      f"engine state loaded: {len(symbols)} symbols (saved {saved_at})")
            return symbols
        except Exception as e:
            self._log("warning", f"engine state load fail: {e}")
            return None

    def _build_client(self):
        from autotrader.broker.binance_testnet_client import (
            BinanceTestnetClient, BinanceTestnetConfig,
        )
        cfg = BinanceTestnetConfig.from_env()
        with self._lock:
            self._status.env = cfg.env
        return BinanceTestnetClient(cfg)

    # ── main loop ─────────────────────────────────────────────────
    def run(self):
        """블로킹 루프. 별도 스레드에서 호출. request_stop() 으로 종료."""
        cfg = self.config
        exit_rule = cfg.resolved_exit_rule()
        symbols = cfg.symbols
        try:
            weights = cfg.normalized_weights()
        except ValueError as e:
            with self._lock:
                self._status.state = "error"
                self._status.error = str(e)
            self._log("error", str(e))
            return

        with self._lock:
            self._status.state = "starting"
            self._status.error = None
            self._status.started_at = datetime.now(timezone.utc).isoformat()
            self._status.mode = cfg.mode
            self._status.exit_rule = exit_rule
            self._started_monotonic = time.monotonic()
        self._emit({"type": "status", "data": self.snapshot()})

        try:
            if self._client is None:
                self._client = self._build_client()
        except Exception as e:
            with self._lock:
                self._status.state = "error"
                self._status.error = f"client init fail: {e}"
            self._log("error", f"client init fail: {e}")
            return
        c = self._client

        direction = "long-short" if cfg.allow_short else "long-only"
        self._log("info", f"=== Crypto Engine — {cfg.mode.upper()} [{direction}] "
                          f"interval={cfg.interval} fast={cfg.fast} slow={cfg.slow} "
                          f"exit={exit_rule} ===")

        # 레버리지 설정
        for sym in symbols:
            try:
                c.set_leverage(sym, cfg.leverage)
            except Exception as e:
                self._log("warning", f"{sym} leverage set fail: {e}")
            if self._stop.is_set():
                break
            time.sleep(0.3)

        # 로그 파일
        if cfg.log_path:
            self._log_path = Path(cfg.log_path)
            self._log_path.parent.mkdir(parents=True, exist_ok=True)

        # 종목 상태 (resume) — 영속화된 state 가 있으면 trail tracking 다 복원
        self._states = {
            sym: SymbolState(symbol=sym, weight=w)
            for sym, w in zip(symbols, weights)
        }
        persisted = self._load_state() or {}
        if persisted:
            # 1단계: 영속화 데이터로 모든 트래킹 필드 복원
            for sym, st in self._states.items():
                if sym not in persisted:
                    continue
                p = persisted[sym]
                st.current_position = p.get("current_position", 0)
                st.entry_price = p.get("entry_price")
                st.high_water = p.get("high_water")
                st.low_water = p.get("low_water")
                st.stopped_until_signal_change = p.get("stopped_until_signal_change", 0)
                st.stopped_at_bar_ts = p.get("stopped_at_bar_ts", 0)
                st.last_evaluated_bar_ts = p.get("last_evaluated_bar_ts", 0)
                st.close_retry_count = p.get("close_retry_count", 0)

        try:
            bal = c.balance()
        except Exception as e:
            with self._lock:
                self._status.state = "error"
                self._status.error = f"balance fetch fail: {e}"
            self._log("error", f"balance fetch fail: {e}")
            return

        starting_wallet = bal["total_wallet_balance"]
        # 2단계: Binance 실체와 sync (영속화 데이터와 충돌 시 보수적 재설정)
        binance_symbols = {p["symbol"]: p for p in bal["positions"]}
        for sym, st in self._states.items():
            p = binance_symbols.get(sym)
            binance_side = 0 if not p else (1 if p["qty"] > 0 else -1 if p["qty"] < 0 else 0)

            if st.current_position == binance_side and binance_side != 0:
                # 영속화 == binance, 그대로 (entry/water 다 복원돼 있음)
                self._log("info",
                          f"resume {sym}: {p['side']} qty={p['qty']} "
                          f"(trail data restored — peak={st.high_water or st.low_water})")
            elif binance_side != 0:
                # 불일치 또는 영속화 없음 → 보수적 초기화
                st.current_position = binance_side
                st.entry_price = float(p.get("entry_price") or 0) or None
                st.high_water = None
                st.low_water = None
                st.close_retry_count = 0
                if exit_rule == "trail" and st.entry_price:
                    try:
                        mark_now = c.quote(sym)["mark_price"]
                    except Exception:
                        mark_now = st.entry_price
                    if binance_side == 1:
                        st.high_water = max(st.entry_price, mark_now or st.entry_price)
                    else:
                        st.low_water = min(st.entry_price, mark_now or st.entry_price)
                self._log("info",
                          f"resume {sym}: {p['side']} qty={p['qty']} (conservative init)")
            elif st.current_position != 0:
                # 영속화는 포지션 있다고 했는데 binance 는 flat → 동기화
                self._log("warning",
                          f"resume {sym}: persisted pos={st.current_position} "
                          f"but binance flat. 정리.")
                st.current_position = 0
                st.entry_price = None
                st.high_water = None
                st.low_water = None
                # stopped_until_signal_change 는 유지 (재진입 차단 룰 보존)

        with self._lock:
            self._status.starting_wallet = starting_wallet
            self._status.wallet_balance = starting_wallet
            self._status.state = "running"
        self._log("info", f"starting wallet: {starting_wallet:,.2f} USDT")
        self._emit({"type": "status", "data": self.snapshot()})

        if cfg.duration_hours <= 0:
            t_end = float("inf")
        else:
            t_end = time.time() + cfg.duration_hours * 3600
        tick = 0

        while time.time() < t_end and not self._stop.is_set():
            now = datetime.now()
            try:
                bal = c.balance()
            except Exception as e:
                self._log("error", f"tick {tick} balance fail: {e}; skip")
                self._interruptible_sleep(cfg.poll_min * 60)
                tick += 1
                continue

            with self._lock:
                self._status.wallet_balance = bal["total_wallet_balance"]
                self._status.margin_balance = bal["total_margin_balance"]
                self._status.unrealized_pnl = bal["total_unrealized_pnl"]
                self._status.pnl = bal["total_wallet_balance"] - starting_wallet
                self._status.pnl_pct = (
                    self._status.pnl / starting_wallet * 100 if starting_wallet else 0)
                self._status.tick = tick

            for sym, st in self._states.items():
                if self._stop.is_set():
                    break
                try:
                    self._process_symbol(c, cfg, exit_rule, sym, st, bal, tick, now)
                except Exception as e:
                    self._log("error",
                              f"tick {tick} [{sym}] {type(e).__name__}: {str(e)[:200]}")

            self._flush_records()
            self._save_state()   # 매 tick trail tracking 영속화
            self._emit({"type": "status", "data": self.snapshot()})
            tick += 1
            self._interruptible_sleep(cfg.poll_min * 60)

        # 종료
        with self._lock:
            self._status.state = "stopping"
        try:
            bal = c.balance()
            final_wallet = bal["total_wallet_balance"]
            pnl = final_wallet - starting_wallet
            pnl_pct = pnl / starting_wallet * 100 if starting_wallet else 0
            with self._lock:
                self._status.wallet_balance = final_wallet
                self._status.pnl = pnl
                self._status.pnl_pct = pnl_pct
            self._log("info",
                      f"=== 종료. final wallet {final_wallet:,.4f} USDT  "
                      f"P&L {pnl:+,.4f} ({pnl_pct:+.4f}%) ===")
        except Exception as e:
            self._log("warning", f"final balance fail: {e}")
        with self._lock:
            self._status.state = "stopped"
        self._emit({"type": "status", "data": self.snapshot()})

    def _interruptible_sleep(self, seconds: float):
        """stop 이벤트가 오면 즉시 깨어나는 sleep."""
        self._stop.wait(timeout=seconds)

    # ── 상태 동기화 / 주문 확인 헬퍼 ───────────────────────────────
    def _reconcile(self, c, sym, st, bal):
        """매 tick 시작 시 Binance 실체와 engine state 비교, 다르면 sync.

        state desync 의 핵심 방어선. close/open 주문이 어떤 이유로든 실제 포지션과
        엔진 인식이 어긋나도 다음 tick 에 자동 복구.
        """
        pos = next((p for p in bal["positions"] if p["symbol"] == sym), None)
        binance_qty = pos["qty"] if pos else 0.0
        binance_side = 1 if binance_qty > 0 else (-1 if binance_qty < 0 else 0)
        if binance_side == st.current_position:
            return

        old = st.current_position
        self._log("warning",
                  f"[{sym}] STATE DESYNC: engine pos={old} != binance qty={binance_qty} "
                  f"(side={binance_side}). 동기화.")
        st.current_position = binance_side
        if binance_side == 0:
            st.entry_price = None
            st.high_water = None
            st.low_water = None
            st.close_retry_count = 0
            return
        new_entry = float(pos.get("entry_price") or 0) or None
        st.entry_price = new_entry
        try:
            cur_mark = c.quote(sym)["mark_price"]
        except Exception:
            cur_mark = new_entry
        if binance_side == 1 and new_entry:
            st.high_water = max(new_entry, cur_mark or new_entry)
            st.low_water = None
        elif binance_side == -1 and new_entry:
            st.low_water = min(new_entry, cur_mark or new_entry)
            st.high_water = None

    def _close_position_confirmed(self, c, sym, st, bal, label, mark) -> bool:
        """청산 주문 + 실제 포지션 변화 확인.

        1. bal["positions"] 가 stale 일 수 있으므로 fresh 한 c.position(sym) 으로 qty 재조회
        2. reduceOnly=True 로 첫 시도 (안전)
        3. -2022 ReduceOnly rejected 받으면 reduceOnly=False 로 재시도
           (testnet 의 알려진 quirk — reduceOnly 가 가용마진 있어도 거부되는 경우)
        4. 주문 후 실제 포지션 변화 확인 → 95% 청산이면 성공

        성공 시 True, 실패/부분 시 False.
        """
        # 1. Fresh position 으로 정확한 qty 추출 (bal 의 stale 데이터 회피)
        live_pos = None
        try:
            live_positions = c.position(sym)
            if live_positions:
                live_pos = live_positions[0]
        except Exception:
            # position() 호출 실패 시 bal 로 fallback
            stale = next((p for p in bal["positions"] if p["symbol"] == sym), None)
            if stale:
                live_pos = {"qty": stale["qty"]}

        if not live_pos or live_pos.get("qty", 0) == 0:
            # 이미 flat (수동 청산 등) — 성공으로 간주
            st.close_retry_count = 0
            return True

        side_close = "sell" if live_pos["qty"] > 0 else "buy"
        qty_to_close = abs(live_pos["qty"])

        order_id = None
        used_fallback = False

        # 2. reduceOnly=True 먼저 (mainnet 안전 기본값)
        try:
            r = c.order(sym, qty=qty_to_close, side=side_close,
                        order_type="MARKET", reduce_only=True)
            order_id = r.get("orderId") if isinstance(r, dict) else None
        except Exception as e:
            err_str = str(e)
            # 3. -2022 발생 시 reduceOnly 없이 재시도 (testnet quirk)
            if "-2022" in err_str or "ReduceOnly" in err_str:
                self._log("warning",
                          f"[{sym}] {label} reduceOnly 거부 (-2022). plain MARKET 으로 fallback.")
                try:
                    r = c.order(sym, qty=qty_to_close, side=side_close,
                                order_type="MARKET", reduce_only=False)
                    order_id = r.get("orderId") if isinstance(r, dict) else None
                    used_fallback = True
                except Exception as e2:
                    st.close_retry_count += 1
                    st.last_order_error = str(e2)[:120]
                    self._log("error",
                              f"[{sym}] {label} fallback 도 실패 (try {st.close_retry_count}): {e2}")
                    if st.close_retry_count >= 10:
                        self._log("error",
                                  f"[{sym}] CRITICAL: 연속 {st.close_retry_count}회 청산 실패. 수동 점검 필요.")
                    return False
            else:
                st.close_retry_count += 1
                st.last_order_error = err_str[:120]
                self._log("error",
                          f"[{sym}] {label} fail (try {st.close_retry_count}): {e}")
                if st.close_retry_count >= 10:
                    self._log("error",
                              f"[{sym}] CRITICAL: 연속 {st.close_retry_count}회 청산 실패. 수동 점검 필요.")
                return False

        note = " [fallback plain]" if used_fallback else ""
        self._log("info",
                  f"[{sym}] {label} {side_close} qty={qty_to_close:.4f} "
                  f"mark={mark:,.2f} → orderId={order_id}{note}")
        time.sleep(2.0)

        # 4. 실제 포지션 변화 확인
        try:
            confirm = c.position(sym)
            new_qty = abs(confirm[0]["qty"]) if confirm else 0.0
        except Exception as e:
            self._log("warning",
                      f"[{sym}] {label} 확인 실패: {e} (다음 tick reconcile 로 검증).")
            st.close_retry_count = 0
            st.last_order_error = None
            return True

        if new_qty < qty_to_close * 0.05:  # 95% 이상 청산
            st.close_retry_count = 0
            st.last_order_error = None
            return True
        else:
            st.close_retry_count += 1
            self._log("warning",
                      f"[{sym}] {label} 부분 청산: 잔여 qty={new_qty:.4f} / "
                      f"시도 {qty_to_close:.4f} (retry {st.close_retry_count})")
            return False

    def _open_position_confirmed(self, c, sym, qty, side_open, target_sig, mark):
        """진입 주문 + 실제 포지션 변화 확인. 성공 시 entry_price 반환, 실패 시 None."""
        try:
            order_resp = c.order(sym, qty=qty, side=side_open, order_type="MARKET")
            order_id = order_resp.get("orderId") if isinstance(order_resp, dict) else None
            self._log("info",
                      f"[{sym}] OPEN {side_open} qty={qty} "
                      f"(~{qty*mark:,.0f} USDT) → orderId={order_id}")
            time.sleep(2.0)
        except Exception as e:
            self._log("error", f"[{sym}] open fail: {e}")
            return None

        try:
            confirm = c.position(sym)
            if not confirm:
                self._log("warning", f"[{sym}] OPEN 후 포지션 0. 진입 미실현으로 간주.")
                return None
            new_qty = confirm[0]["qty"]
            new_side = 1 if new_qty > 0 else (-1 if new_qty < 0 else 0)
            if new_side == target_sig:
                entry_actual = float(confirm[0].get("entry") or mark) or mark
                return entry_actual
            else:
                self._log("warning",
                          f"[{sym}] OPEN 방향 불일치: 기대 sig={target_sig}, "
                          f"binance qty={new_qty}. 진입 실패로 간주.")
                return None
        except Exception as e:
            self._log("warning", f"[{sym}] OPEN 확인 실패: {e}. 성공 가정.")
            return mark

    # ── 종목 단위 메인 처리 ───────────────────────────────────────
    def _process_symbol(self, c, cfg, exit_rule, sym, st, bal, tick, now):
        # 0. RECONCILE — Binance 실체와 동기화 (state desync 방어선)
        self._reconcile(c, sym, st, bal)

        # 1. 신호 계산 — 옵션 Z: closed bar 만 사용
        kl = c.klines(sym, interval=cfg.interval, limit=cfg.slow + 5)
        interval_ms = INTERVAL_MS.get(cfg.interval, 3_600_000)
        now_ms = int(time.time() * 1000)
        closed_kl = [k for k in kl if int(k[0]) + interval_ms <= now_ms]
        if len(closed_kl) < cfg.slow:
            fast_ma = slow_ma = 0
            target_sig = 0
            new_bar_closed = False
            last_closed_ts = 0
        else:
            fast_ma, slow_ma, target_sig = compute_signal(closed_kl, cfg.fast, cfg.slow)
            if not cfg.allow_short and target_sig < 0:
                target_sig = 0
            last_closed_ts = int(closed_kl[-1][0])
            new_bar_closed = (last_closed_ts > st.last_evaluated_bar_ts)

        q = c.quote(sym)
        mark = q["mark_price"]
        st.mark_price = mark
        st.fast_ma = fast_ma
        st.slow_ma = slow_ma
        st.target_signal = target_sig

        action = "HOLD"

        # 2. trail 청산 (옵션 Z: 봉 마감 시점)
        trail_triggered = False
        if exit_rule == "trail" and st.current_position != 0 and new_bar_closed:
            bar_close = float(closed_kl[-1][4])
            if st.current_position == 1:
                st.high_water = max(st.high_water or bar_close, bar_close)
                if bar_close < st.high_water * (1 - cfg.trail_pct):
                    trail_triggered = True
            elif st.current_position == -1:
                st.low_water = min(st.low_water or bar_close, bar_close)
                if bar_close > st.low_water * (1 + cfg.trail_pct):
                    trail_triggered = True

        # 청산 시도 — trail 발동 또는 직전 tick 의 실패 재시도
        needs_close = (exit_rule == "trail"
                        and st.current_position != 0
                        and (trail_triggered or st.close_retry_count > 0))
        if needs_close:
            target_was = st.current_position
            if self._close_position_confirmed(c, sym, st, bal, "TRAIL-STOP", mark):
                action = f"TRAIL-STOP {target_was}→0"
                st.stopped_until_signal_change = target_was
                # cooldown 정책용 — 청산된 봉의 open_time 저장 (재진입 차단 기준점)
                st.stopped_at_bar_ts = last_closed_ts
                st.current_position = 0
                st.entry_price = None
                st.high_water = None
                st.low_water = None
                st.last_action_tick = tick
            else:
                action = f"TRAIL-STOP RETRY({st.close_retry_count})"
                # state 유지. 다음 tick 의 reconcile + 재시도가 마무리.

        # 3. signal 기반 액션
        should_act = False
        if exit_rule == "flip":
            should_act = (target_sig != st.current_position)
        else:
            if new_bar_closed and not trail_triggered and st.close_retry_count == 0:
                # 차단 해제 정책 — backtest 검증: cooldown 1봉 = Sharpe +6.5
                if cfg.block_policy == "cooldown":
                    if st.stopped_until_signal_change != 0:
                        # 이전 정책에서 넘어와 stopped_at_bar_ts 없으면 즉시 해제
                        if st.stopped_at_bar_ts == 0:
                            st.stopped_until_signal_change = 0
                        else:
                            interval_ms = INTERVAL_MS.get(cfg.interval, 14_400_000)
                            bars_since_stop = (last_closed_ts - st.stopped_at_bar_ts) // interval_ms
                            if bars_since_stop >= cfg.cooldown_bars:
                                st.stopped_until_signal_change = 0
                                st.stopped_at_bar_ts = 0
                else:
                    # signal_change 정책 (기존, opt-Y/Z 검증)
                    if target_sig != st.stopped_until_signal_change:
                        st.stopped_until_signal_change = 0
                        st.stopped_at_bar_ts = 0
                should_act = (st.current_position == 0
                              and target_sig != 0
                              and target_sig != st.stopped_until_signal_change)
                st.last_evaluated_bar_ts = last_closed_ts

        if should_act:
            # flip 청산 (intraday 모드)
            if exit_rule == "flip" and st.current_position != 0:
                target_was = st.current_position
                if not self._close_position_confirmed(c, sym, st, bal, "FLIP-CLOSE", mark):
                    action = f"FLIP CLOSE_FAIL ({st.close_retry_count})"
                    st.last_action = action
                    return
                action = f"FLIP {target_was}→{target_sig}"
                st.current_position = 0
            else:
                action = f"OPEN 0→{target_sig}"

            # 진입
            if target_sig != 0:
                bal = c.balance()
                total_margin = bal["total_margin_balance"]
                my_capital = total_margin * st.weight
                notional = my_capital * cfg.position_pct
                qty_raw = notional / mark * cfg.leverage
                qty = round_qty(sym, qty_raw)
                if qty <= 0:
                    self._log("warning",
                              f"[{sym}] qty={qty} 너무 작음 (자본 부족 / 종목 과다 분할)")
                else:
                    side_open = "buy" if target_sig > 0 else "sell"
                    entry_actual = self._open_position_confirmed(
                        c, sym, qty, side_open, target_sig, mark)
                    if entry_actual is not None:
                        st.current_position = target_sig
                        if exit_rule == "trail":
                            st.entry_price = entry_actual
                            st.high_water = entry_actual if target_sig == 1 else None
                            st.low_water = entry_actual if target_sig == -1 else None
                        st.last_action_tick = tick
                    else:
                        action = "OPEN_FAIL"

        st.last_action = action

        rec = {
            "ts": now.isoformat(),
            "tick": tick,
            "symbol": sym,
            "weight": st.weight,
            "mark_price": mark,
            "fast_ma": fast_ma,
            "slow_ma": slow_ma,
            "target_signal": target_sig,
            "current_position": st.current_position,
            "entry_price": st.entry_price,
            "high_water": st.high_water,
            "low_water": st.low_water,
            "exit_rule": exit_rule,
            "action": action,
            "close_retry": st.close_retry_count,
        }
        self._log_records.append(rec)

        if tick % 5 == 0 or action != "HOLD":
            extra = ""
            if exit_rule == "trail" and st.current_position != 0 and st.entry_price:
                peak = st.high_water if st.current_position == 1 else st.low_water
                if peak:
                    dist = ((mark - peak) / peak * 100 if st.current_position == 1
                            else (peak - mark) / peak * 100)
                    extra = f" peak={peak:,.2f} dist={dist:+.2f}%"
            if st.close_retry_count > 0:
                extra += f" close_retry={st.close_retry_count}"
            self._log("info",
                      f"t={tick} [{sym}] mark={mark:,.2f} fast={fast_ma:,.2f} "
                      f"slow={slow_ma:,.2f} sig={target_sig} pos={st.current_position} "
                      f"{action}{extra}")
