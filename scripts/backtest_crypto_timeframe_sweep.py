"""Crypto MA timeframe sweep — bar interval × MA period 조합 비교.

질문: "현재 6h/24h MA 가 변동성 대응에 느린가?"

Test matrix:
  - Bar interval:    1h, 5m
  - MA (fast, slow): (3,12), (6,24)*, (12,48), (24,96), (48,192), (72,288)
  - Strategy:        B_trend (long-short, 가장 단순)
  - Overlay:         Trail2 (-2%)
  - Symbols:         BTC, ETH, SOL, AVAX, BNB
  - Period:          60일

각 조합의 Sharpe / Return / MaxDD / Flips 비교 → 최적 timeframe 찾기.
"""

from __future__ import annotations

import argparse
import json
import sys
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

from autotrader.data.crypto_bars import fetch_crypto_bars
from autotrader.data.crypto_signals import fetch_funding_rate
from autotrader.backtest.crypto_strategies import (
    BacktestConfig, backtest, summarize, merge_signals,
    apply_risk_overlay, strat_trend,
)


SYMBOL_MAP = {
    "BTC/USDT:USDT": "BTCUSDT",
    "ETH/USDT:USDT": "ETHUSDT",
    "SOL/USDT:USDT": "SOLUSDT",
    "AVAX/USDT:USDT": "AVAXUSDT",
    "BNB/USDT:USDT": "BNBUSDT",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()

    # Test matrix
    INTERVALS = [
        # (bar_interval, bars_per_year, MA combos as (fast, slow, label))
        ("1h", 8760, [
            (3, 12,  "3h/12h"),
            (6, 24,  "6h/24h*"),  # current default
            (12, 48, "12h/48h"),
            (24, 96, "1d/4d"),
        ]),
        ("5m", 105120, [
            (12, 48,   "1h/4h"),
            (24, 96,   "2h/8h"),
            (48, 192,  "4h/16h"),
            (72, 288,  "6h/24h"),    # 1h base 의 6/24 와 동일 의미
            (144, 576, "12h/48h"),
        ]),
    ]

    print(f"=== MA Timeframe Sweep — {args.days}d ===")
    rows = []

    for interval, bpy, ma_combos in INTERVALS:
        print(f"\n--- Interval: {interval} ---")
        for ccxt_sym in SYMBOL_MAP.keys():
            binance_sym = SYMBOL_MAP[ccxt_sym]

            try:
                price = fetch_crypto_bars(ccxt_sym, timeframe=interval, days=args.days)
            except Exception as e:
                print(f"  {ccxt_sym}: fetch fail {e}"); continue
            if price.empty or len(price) < 300:
                print(f"  {ccxt_sym}: insufficient data ({len(price)} bars)"); continue
            print(f"  {ccxt_sym}: {len(price):>6} bars")

            for fast, slow, label in ma_combos:
                if len(price) < slow + 5:
                    continue
                cfg = BacktestConfig(fast_ma=fast, slow_ma=slow,
                                      bb_window=20, bars_per_year=bpy)
                df = price.copy()
                # Run B_trend (no funding needed)
                base_pos = strat_trend(df, cfg)
                # Apply Trail2 (best from prior sweep)
                pos = apply_risk_overlay(df, base_pos, trailing_stop_pct=0.02)
                out = backtest(df, pos, cfg)
                s = summarize(out, cfg)
                rows.append({
                    "symbol": binance_sym,
                    "interval": interval,
                    "fast_ma": fast,
                    "slow_ma": slow,
                    "ma_label": label,
                    **s,
                })

    if not rows:
        print("\nNo results."); return

    df_results = pd.DataFrame(rows)
    df_results = df_results.dropna(subset=["sharpe"])

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = ROOT / "data" / f"backtest_crypto_timeframe_{args.days}d_{ts}.parquet"
    df_results.to_parquet(out_path)
    print(f"\n[saved] {out_path}")

    # ──────────────────────────────────────────────── Best per (symbol, interval)
    print()
    print("=" * 110)
    print("Best MA combo per (symbol, interval):")
    print("=" * 110)
    print(f"{'symbol':>8} {'interval':>4} {'ma_label':>10} {'return%':>8} {'Sharpe':>7} {'maxDD%':>8} {'flips':>6}")
    print("-" * 110)
    best = df_results.loc[df_results.groupby(["symbol", "interval"])["sharpe"].idxmax()]
    for _, r in best.sort_values(["symbol", "interval"]).iterrows():
        print(f"{r['symbol']:>8} {r['interval']:>4} {r['ma_label']:>10} {r['total_return_pct']:>+8.2f} {r['sharpe']:>+7.2f} {r['max_drawdown_pct']:>+8.2f} {int(r['n_position_flips']):>6}")

    # ──────────────────────────────────────────────── Average per (interval, MA)
    print()
    print("=" * 90)
    print("Average per (interval, MA combo) — across all 5 symbols:")
    print("=" * 90)
    print(f"{'interval':>4} {'ma_label':>10} {'avg_return%':>11} {'avg_Sharpe':>11} {'avg_maxDD%':>11} {'avg_flips':>10}")
    print("-" * 90)
    by_combo = df_results.groupby(["interval", "ma_label"]).agg(
        avg_return=("total_return_pct", "mean"),
        avg_sharpe=("sharpe", "mean"),
        avg_dd=("max_drawdown_pct", "mean"),
        avg_flips=("n_position_flips", "mean"),
    ).reset_index().sort_values("avg_sharpe", ascending=False)
    for _, r in by_combo.iterrows():
        print(f"{r['interval']:>4} {r['ma_label']:>10} {r['avg_return']:>+11.2f} {r['avg_sharpe']:>+11.2f} {r['avg_dd']:>+11.2f} {r['avg_flips']:>10.0f}")

    # JSON summary
    summary = {
        "best_per_symbol_interval": best.to_dict("records"),
        "avg_per_combo": by_combo.to_dict("records"),
        "results": rows,
    }
    summary_path = ROOT / "data" / f"backtest_crypto_timeframe_{args.days}d_{ts}_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n[saved] summary: {summary_path}")


if __name__ == "__main__":
    main()
