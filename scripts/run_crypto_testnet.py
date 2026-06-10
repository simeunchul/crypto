"""Crypto MA crossover live runner — Binance Testnet (multi-symbol).

전략: (fast, slow) MA crossover, 다중 종목. 두 가지 운영 모드:

  --mode intraday  — 신호 반전 시 즉시 청산 (FLIP). 짧고 잦은 트레이드.
  --mode swing     — 고점 대비 -trail_pct% trailing stop 청산 (winners run).
                     365d backtest Sharpe +2.76 검증 구성.

루프 본체는 autotrader.live.BotEngine 으로 분리되어 서버 / 데스크톱 앱과 공유한다.
이 스크립트는 CLI 진입점 (argparse + graceful shutdown) 역할만 한다.

Graceful shutdown: Ctrl+C
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

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

from autotrader.live import BotConfig, BotEngine


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT",
                    help="콤마 구분 종목 (default BTCUSDT,ETHUSDT)")
    ap.add_argument("--capital-split", default=None,
                    help="콤마 구분 weights (default 균등 분할)")
    ap.add_argument("--mode", choices=["intraday", "swing"], default="intraday")
    ap.add_argument("--exit-rule", choices=["flip", "trail"], default=None)
    ap.add_argument("--trail-pct", type=float, default=0.02)
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--fast", type=int, default=6)
    ap.add_argument("--slow", type=int, default=24)
    ap.add_argument("--allow-short", action="store_true")
    ap.add_argument("--leverage", type=int, default=1)
    ap.add_argument("--position-pct", type=float, default=0.95)
    ap.add_argument("--poll-min", type=float, default=30)
    ap.add_argument("--duration-hours", type=float, default=24,
                    help="0 또는 음수 = 무한")
    ap.add_argument("--block-policy", choices=["signal_change", "cooldown"],
                    default="signal_change",
                    help="trail-stop 후 재진입 차단 정책. "
                         "signal_change=신호 바뀔 때까지 (기존). "
                         "cooldown=N봉 후 자동 해제 (backtest 검증 +6.5 Sharpe).")
    ap.add_argument("--cooldown-bars", type=int, default=1,
                    help="block-policy=cooldown 일 때 차단 봉 수 (interval 단위). "
                         "기본 1봉 = 4h interval 에서 4시간.")
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    weights = ([float(w) for w in args.capital_split.split(",")]
               if args.capital_split else None)

    today = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = args.log or str(ROOT / "data" / f"crypto_testnet_log_{today}.json")

    cfg = BotConfig(
        symbols=symbols,
        weights=weights,
        mode=args.mode,
        exit_rule=args.exit_rule,
        trail_pct=args.trail_pct,
        interval=args.interval,
        fast=args.fast,
        slow=args.slow,
        allow_short=args.allow_short,
        leverage=args.leverage,
        position_pct=args.position_pct,
        poll_min=args.poll_min,
        duration_hours=args.duration_hours,
        log_path=log_path,
        block_policy=args.block_policy,
        cooldown_bars=args.cooldown_bars,
    )

    engine = BotEngine(cfg)

    def _sig(*_):
        print("\n[graceful] SIGINT received — shutting down...", flush=True)
        engine.request_stop()

    signal.signal(signal.SIGINT, _sig)
    try:
        signal.signal(signal.SIGTERM, _sig)
    except Exception:
        pass

    engine.run()


if __name__ == "__main__":
    main()
