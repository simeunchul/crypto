"""Crypto live runner — per-symbol strategy 분배.

365일 backtest 결과 기준 종목별 best strategy 자동 적용:
  BTCUSDT  → G_bollinger_mr        (+24.07%, Sharpe 0.84)
  ETHUSDT  → A_buyhold              (+22.20%, Sharpe 0.63)
  SOLUSDT  → H_funding_contra      (+14.09%, Sharpe 1.08)
  AVAXUSDT → D_trend+funding       (+159.58%, Sharpe 1.60)
  BNBUSDT  → D_trend+funding       (+105.64%, Sharpe 1.67)

각 종목 독립 신호 + capital weight 분할.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.split("#", 1)[0].strip()
    if "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from autotrader.broker.binance_testnet_client import (
    BinanceTestnetClient, BinanceTestnetConfig,
)
from autotrader.backtest.crypto_strategies import (
    BacktestConfig, STRATEGIES,
)


# ── 365d backtest + Trailing stop sweep 결과 기준.
# 각 (strategy, trail_pct) 가 365d 1위.
DEFAULT_ASSIGNMENT = {
    # symbol:    (strategy, trailing_stop_pct)
    "BTCUSDT":  ("B_trend",            0.02),  # +40%, Sharpe 2.92
    "ETHUSDT":  ("C_trend_long_only",  0.02),  # +102%, Sharpe 3.40
    "SOLUSDT":  ("D_trend+funding",    0.02),  # +95%, Sharpe 3.31
    "AVAXUSDT": ("B_trend",            0.03),  # +261%, Sharpe 4.54
    "BNBUSDT":  ("D_trend+funding",    0.02),  # +83%, Sharpe 3.61
}

# ── Binance Futures qty precision (LOT_SIZE filter 기준)
SYMBOL_QTY_PRECISION = {
    "BTCUSDT":  3,    # step 0.001 (실제 0.0001 가능하지만 안전하게 3)
    "ETHUSDT":  3,    # step 0.001
    "BNBUSDT":  2,    # step 0.01
    "SOLUSDT":  2,    # step 0.01  (이전엔 1 이었지만 0.01 가능)
    "AVAXUSDT": 0,    # step 1 (정수만)
}


class GracefulExit:
    def __init__(self):
        self.shutdown = False
        signal.signal(signal.SIGINT, self._sig)
        try:
            signal.signal(signal.SIGTERM, self._sig)
        except Exception:
            pass

    def _sig(self, *_):
        print("\n[graceful] shutting down...", flush=True)
        self.shutdown = True


@dataclass
class SymbolState:
    symbol: str
    strategy_name: str
    weight: float
    trailing_stop_pct: float = 0.0   # 0 = no trailing
    current_position: int = 0
    entry_price: float | None = None
    high_water: float | None = None    # for LONG trailing
    low_water: float | None = None     # for SHORT trailing
    stopped_until_signal_change: int = 0   # remember signal we stopped on


def _funding_rate_now(client: BinanceTestnetClient, symbol: str) -> float:
    """가장 최근 funding rate (직전 정산값)."""
    try:
        # Mark price endpoint includes lastFundingRate
        m = client._client.futures_mark_price(symbol=symbol)
        return float(m.get("lastFundingRate", 0.0))
    except Exception:
        return 0.0


def _compute_signal(client: BinanceTestnetClient, sym: str,
                     strategy_name: str, cfg: BacktestConfig) -> tuple[int, dict]:
    """klines + (필요시) funding 받아서 strategy 함수 통해 signal 계산.

    Returns: (target_sig, debug_info)
    """
    debug = {"strategy": strategy_name}

    # 1h klines (충분한 lookback)
    lookback = max(cfg.slow_ma + 5, cfg.bb_window + 5, 30)
    kl = client.klines(sym, interval="1h", limit=lookback)
    if not kl:
        return 0, {**debug, "error": "no klines"}

    closes = [float(k[4]) for k in kl]
    df = pd.DataFrame({"close": closes})

    # Funding rate 필요한 strategy 만 페치
    if strategy_name in ("D_trend+funding", "F_trend+3factor", "H_funding_contra"):
        funding = _funding_rate_now(client, sym)
        df["funding_rate"] = funding   # 모든 row 같은 값 (latest)
        debug["funding"] = funding

    # L/S ratio 필요한 strategy (E, F) — testnet 데이터 부재 → skip
    if strategy_name in ("E_trend+lsratio", "F_trend+3factor"):
        # placeholder 0.5 (중립) 으로 설정 → filter 효과 없음
        df["long_pct"] = 0.5
        df["short_pct"] = 0.5
        df["ls_account_ratio"] = 1.0

    # Strategy 실행
    if strategy_name not in STRATEGIES:
        return 0, {**debug, "error": f"unknown strategy {strategy_name}"}
    strat_fn = STRATEGIES[strategy_name]
    try:
        position_series = strat_fn(df, cfg)
        target_sig = int(round(float(position_series.iloc[-1])))
    except Exception as e:
        return 0, {**debug, "error": f"{type(e).__name__}: {str(e)[:100]}"}

    # MA 정보 (디버깅용)
    if cfg.fast_ma <= len(closes):
        debug["fast_ma"] = sum(closes[-cfg.fast_ma:]) / cfg.fast_ma
    if cfg.slow_ma <= len(closes):
        debug["slow_ma"] = sum(closes[-cfg.slow_ma:]) / cfg.slow_ma
    debug["last_close"] = closes[-1]

    return target_sig, debug


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(DEFAULT_ASSIGNMENT.keys()),
                    help="콤마 구분 종목 (default: 5종목 backtest 결과)")
    ap.add_argument("--strategies", default=None,
                    help="콤마 구분 strategy 이름 (각 symbol 대응). 미지정 시 DEFAULT_ASSIGNMENT 사용")
    ap.add_argument("--leverage", type=int, default=1)
    ap.add_argument("--position-pct", type=float, default=0.95)
    ap.add_argument("--poll-min", type=float, default=30)
    ap.add_argument("--duration-hours", type=float, default=24)
    ap.add_argument("--trailing-only", action="store_true",
                    help="signal 반전 시 청산 X — trailing stop 만으로 청산 (default: hybrid)")
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger("crypto-per-symbol")

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if args.strategies:
        # legacy: --strategies "B_trend,C_trend_long_only,..."
        strat_list = [s.strip() for s in args.strategies.split(",")]
        if len(strat_list) != len(symbols):
            logger.error(f"strategies len ({len(strat_list)}) != symbols len ({len(symbols)})")
            sys.exit(1)
        assignment = {s: (st, 0.02) for s, st in zip(symbols, strat_list)}  # default 2% trail
    else:
        # DEFAULT_ASSIGNMENT 은 (strategy, trail_pct) tuple
        assignment = {}
        for s in symbols:
            v = DEFAULT_ASSIGNMENT.get(s, ("C_trend_long_only", 0.02))
            assignment[s] = v if isinstance(v, tuple) else (v, 0.02)

    # 동등 weight (자본 균등 분할)
    weights = {s: 1.0 / len(symbols) for s in symbols}

    cfg = BacktestConfig()
    c = BinanceTestnetClient(BinanceTestnetConfig.from_env())

    mode = "TRAILING-ONLY" if args.trailing_only else "HYBRID"
    logger.info(f"=== Per-Symbol Strategy Runner [{mode}] ===")
    for sym in symbols:
        strat, trail = assignment[sym]
        logger.info(f"  {sym:>10s} → {strat:>22s} + Trail{int(trail*100)}%  weight={weights[sym]:.0%}  lev={args.leverage}x")
    logger.info(f"  poll={args.poll_min}min  duration={args.duration_hours}h  exit_mode={mode}")
    if args.trailing_only:
        logger.info(f"  → signal 반전 시 청산 X. trailing stop 도달 시만 청산.")

    # 레버리지 설정
    for sym in symbols:
        try:
            c.set_leverage(sym, args.leverage)
        except Exception as e:
            logger.warning(f"  {sym} leverage set fail: {e}")
        time.sleep(0.3)

    # 로그
    today = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = Path(args.log) if args.log else (
        ROOT / "data" / f"crypto_per_symbol_log_{today}.json"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_records: list[dict] = []

    def _flush():
        tmp = log_path.with_suffix(log_path.suffix + ".tmp")
        tmp.write_text(json.dumps(log_records, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        tmp.replace(log_path)

    # 상태 초기화 (resume)
    states = {}
    for sym in symbols:
        strat, trail = assignment[sym]
        states[sym] = SymbolState(
            symbol=sym, strategy_name=strat,
            weight=weights[sym], trailing_stop_pct=trail,
        )
    bal = c.balance()
    starting_wallet = bal["total_wallet_balance"]
    for p in bal["positions"]:
        if p["symbol"] in states:
            states[p["symbol"]].current_position = (
                1 if p["qty"] > 0 else (-1 if p["qty"] < 0 else 0)
            )
            logger.info(f"  resume {p['symbol']}: {p['side']} qty={p['qty']}")
    logger.info(f"  starting wallet: {starting_wallet:,.2f} USDT")

    exit_handler = GracefulExit()
    t_end = time.time() + args.duration_hours * 3600
    tick = 0

    while time.time() < t_end and not exit_handler.shutdown:
        now = datetime.now()
        try:
            bal = c.balance()
        except Exception as e:
            logger.error(f"tick {tick} balance fail: {e}; skip")
            time.sleep(args.poll_min * 60)
            tick += 1
            continue

        for sym, st in states.items():
            try:
                base_sig, debug = _compute_signal(c, sym, st.strategy_name, cfg)
                q = c.quote(sym)
                mark = q["mark_price"]

                action = "HOLD"
                order_resp = None
                target_sig = base_sig
                trail_triggered = False

                # ── Trailing stop check (보유 중일 때만)
                if st.current_position != 0 and st.entry_price is not None and st.trailing_stop_pct > 0:
                    trail = st.trailing_stop_pct
                    if st.current_position == 1:    # LONG
                        st.high_water = max(st.high_water or mark, mark)
                        stop_price = st.high_water * (1 - trail)
                        if mark < stop_price:
                            target_sig = 0   # force exit
                            trail_triggered = True
                            debug["trail_stop"] = f"LONG hi={st.high_water:.4f} stop={stop_price:.4f}"
                    elif st.current_position == -1:   # SHORT
                        st.low_water = min(st.low_water or mark, mark)
                        stop_price = st.low_water * (1 + trail)
                        if mark > stop_price:
                            target_sig = 0
                            trail_triggered = True
                            debug["trail_stop"] = f"SHORT lo={st.low_water:.4f} stop={stop_price:.4f}"

                # ── TRAILING-ONLY 모드: 보유 중이면 base signal 무시
                if args.trailing_only and st.current_position != 0 and not trail_triggered:
                    target_sig = st.current_position   # signal 무관, 그대로 유지
                    debug["trail_only_hold"] = True

                # ── Stop-out 후 재진입 차단: base signal 같은 방향이면 무시
                if not trail_triggered and st.stopped_until_signal_change != 0:
                    if base_sig == st.stopped_until_signal_change:
                        target_sig = 0
                        debug["blocked"] = f"stopped_at_sig={st.stopped_until_signal_change}"
                    elif base_sig != st.stopped_until_signal_change:
                        st.stopped_until_signal_change = 0   # reset

                if target_sig != st.current_position:
                    if trail_triggered:
                        action = f"TRAIL_STOP {st.current_position}→0"
                    else:
                        action = f"FLIP {st.current_position}→{target_sig}"

                    # 청산
                    if st.current_position != 0:
                        pos = next((p for p in bal["positions"] if p["symbol"] == sym), None)
                        if pos:
                            side_close = "sell" if pos["qty"] > 0 else "buy"
                            try:
                                r = c.order(sym, qty=abs(pos["qty"]),
                                             side=side_close, order_type="MARKET",
                                             reduce_only=True)
                                logger.info(f"  [{sym}] CLOSE {side_close} {abs(pos['qty']):.4f} → {r.get('orderId')} ({action})")
                                time.sleep(1.5)
                            except Exception as e:
                                logger.error(f"  [{sym}] close fail: {e}")

                        # trailing stop trigger 시 같은 방향 base signal 재진입 차단
                        if trail_triggered:
                            st.stopped_until_signal_change = st.current_position
                        else:
                            st.stopped_until_signal_change = 0

                        # reset water marks
                        st.high_water = None
                        st.low_water = None
                        st.entry_price = None

                    # 진입
                    if target_sig != 0:
                        bal_now = c.balance()
                        my_capital = bal_now["total_margin_balance"] * st.weight
                        notional = my_capital * args.position_pct
                        qty_raw = notional / mark * args.leverage
                        precision = SYMBOL_QTY_PRECISION.get(sym, 3)
                        qty = round(qty_raw, precision)
                        if precision == 0:
                            qty = int(qty)
                        if qty <= 0:
                            logger.warning(f"  [{sym}] qty too small")
                        else:
                            side_open = "buy" if target_sig > 0 else "sell"
                            try:
                                order_resp = c.order(sym, qty=qty, side=side_open,
                                                      order_type="MARKET")
                                logger.info(f"  [{sym}] OPEN {side_open} {qty} (~{qty*mark:,.0f} USDT) {st.strategy_name}+Trail{int(st.trailing_stop_pct*100)}%")
                                time.sleep(1.5)
                                # entry tracking 시작
                                st.entry_price = mark
                                st.high_water = mark if target_sig == 1 else None
                                st.low_water = mark if target_sig == -1 else None
                            except Exception as e:
                                logger.error(f"  [{sym}] open fail: {e}")
                                order_resp = {"error": str(e)[:120]}

                    st.current_position = target_sig

                rec = {
                    "ts": now.isoformat(),
                    "tick": tick,
                    "symbol": sym,
                    "strategy": st.strategy_name,
                    "trailing_pct": st.trailing_stop_pct,
                    "weight": st.weight,
                    "mark_price": mark,
                    "base_signal": base_sig,
                    "target_signal": target_sig,
                    "current_position": st.current_position,
                    "entry_price": st.entry_price,
                    "high_water": st.high_water,
                    "low_water": st.low_water,
                    "action": action,
                    "debug": debug,
                    "order_response": order_resp,
                }
                log_records.append(rec)

                if tick % 5 == 0 or action != "HOLD":
                    extra = ""
                    if "fast_ma" in debug and "slow_ma" in debug:
                        extra = f"fast={debug['fast_ma']:.2f} slow={debug['slow_ma']:.2f}"
                    elif "funding" in debug:
                        extra = f"funding={debug['funding']*100:+.4f}%"
                    logger.info(f"t={tick} [{sym} {st.strategy_name}] mark={mark:>10,.2f} {extra} sig={target_sig} pos={st.current_position} {action}")

            except Exception as e:
                logger.error(f"tick {tick} [{sym}] error: {type(e).__name__}: {str(e)[:200]}")

        _flush()
        tick += 1
        time.sleep(args.poll_min * 60)

    # 종료
    logger.info(f"=== 종료. {len(log_records)} records → {log_path}")
    bal = c.balance()
    final_wallet = bal["total_wallet_balance"]
    pnl = final_wallet - starting_wallet
    pnl_pct = pnl / starting_wallet * 100 if starting_wallet else 0
    logger.info(f"  최종 wallet: {final_wallet:,.4f} USDT")
    logger.info(f"  P&L: {pnl:+,.4f} USDT ({pnl_pct:+.4f}%)")
    for p in bal["positions"]:
        logger.info(f"  {p['symbol']:>10s} qty={p['qty']:>10.4f} entry={p['entry_price']:,.2f} upnl={p['unrealized_pnl']:+.4f}")

    flips_per_sym = {}
    for r in log_records:
        if r["action"] != "HOLD":
            flips_per_sym[r["symbol"]] = flips_per_sym.get(r["symbol"], 0) + 1
    logger.info(f"  Flips per symbol: {flips_per_sym}")


if __name__ == "__main__":
    main()
