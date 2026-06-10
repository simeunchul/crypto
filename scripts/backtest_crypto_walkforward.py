"""Walk-forward 분석 — long-short trend-follow 가 "꾸준한가" vs "추세 의존인가".

질문 (사용자):
  "지금 2일 수익이 일시적인가? long-short 오가며 장기 꾸준 수익 가능한가?"

방법:
  - 365일 4h 데이터를 30일 비중첩 윈도우 12개로 분할
  - 각 윈도우에서 4h+12/48+trail2 (long-short) 성과 측정
  - 각 윈도우의 시장 국면 분류 (buy&hold 수익률 = 추세 강도)

해석:
  - 모든 윈도우 +수익          → 꾸준 (강건)
  - 추세 윈도우만 +, 횡보 -    → trend-follow 본성 (추세 의존)
  - 대부분 -, 한두 개만 +      → 운 (위험)

전체 365d position 을 한 번 계산 후 (warmup 자동 처리) 윈도우별로 성과 분해.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from autotrader.data.crypto_bars import fetch_crypto_bars
from autotrader.backtest.crypto_strategies import (
    BacktestConfig, backtest, apply_risk_overlay, strat_trend,
)


SYMBOLS = [
    ("BTC/USDT:USDT",  "BTCUSDT"),
    ("ETH/USDT:USDT",  "ETHUSDT"),
    ("SOL/USDT:USDT",  "SOLUSDT"),
    ("AVAX/USDT:USDT", "AVAXUSDT"),
    ("BNB/USDT:USDT",  "BNBUSDT"),
    ("OP/USDT:USDT",   "OPUSDT"),
    ("SUI/USDT:USDT",  "SUIUSDT"),
    ("NEAR/USDT:USDT", "NEARUSDT"),
    ("INJ/USDT:USDT",  "INJUSDT"),
    ("ARB/USDT:USDT",  "ARBUSDT"),
]

BARS_PER_YEAR_4H = 2190
BARS_PER_DAY_4H = 6   # 24h / 4h


def classify_regime(bnh_pct: float) -> str:
    """구간 buy&hold 수익률로 시장 국면 분류."""
    a = abs(bnh_pct)
    if a >= 15:
        return "STRONG_TREND"   # 강한 추세 (상승 or 하락)
    elif a >= 6:
        return "WEAK_TREND"
    else:
        return "RANGE"          # 횡보


def window_metrics(sub: pd.DataFrame, bnh_pct: float) -> dict:
    """한 윈도우 구간의 성과 측정."""
    rets = sub["net_ret"]
    if len(sub) < 2 or rets.std() == 0:
        return {}
    # 윈도우 내 누적 수익 (equity 재기준화)
    eq = (1 + rets).cumprod()
    total_ret = float(eq.iloc[-1] - 1)
    sharpe = float(rets.mean() / rets.std() * np.sqrt(BARS_PER_YEAR_4H))
    max_dd = float(((eq / eq.cummax()) - 1).min())
    n_flips = int((sub["position"].diff().abs() > 0).sum())
    return {
        "window_return_pct": total_ret * 100,
        "sharpe": sharpe,
        "max_dd_pct": max_dd * 100,
        "n_flips": n_flips,
        "bnh_pct": bnh_pct,
        "regime": classify_regime(bnh_pct),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--trail-pct", type=float, default=0.02)
    ap.add_argument("--fast", type=int, default=12)
    ap.add_argument("--slow", type=int, default=48)
    args = ap.parse_args()

    print(f"=== Walk-forward — {args.days}d, {args.window_days}d 윈도우, long-short 4h+{args.fast}/{args.slow}+trail{args.trail_pct:.0%} ===")

    cfg = BacktestConfig(fast_ma=args.fast, slow_ma=args.slow,
                         cost_bps_per_side=4.0, bars_per_year=BARS_PER_YEAR_4H)
    window_bars = args.window_days * BARS_PER_DAY_4H

    all_rows = []

    for ccxt_sym, binance_sym in SYMBOLS:
        try:
            df = fetch_crypto_bars(ccxt_sym, timeframe="4h", days=args.days)
        except Exception as e:
            print(f"  {binance_sym}: fetch fail {e}"); continue
        if df.empty or len(df) < args.slow + window_bars:
            print(f"  {binance_sym}: insufficient ({len(df)} bars)"); continue

        # 전체 기간 position + backtest (warmup 자동)
        base_pos = strat_trend(df, cfg)   # long-short (+1 / -1)
        pos = apply_risk_overlay(df, base_pos, trailing_stop_pct=args.trail_pct)
        out = backtest(df, pos, cfg)

        # 30일 비중첩 윈도우로 분해
        n = len(out)
        n_windows = n // window_bars
        for w in range(n_windows):
            lo = w * window_bars
            hi = lo + window_bars
            sub = out.iloc[lo:hi]
            if len(sub) < 2:
                continue
            # 구간 buy&hold (close 시작→끝)
            bnh = float((sub["close"].iloc[-1] / sub["close"].iloc[0] - 1) * 100)
            m = window_metrics(sub, bnh)
            if not m:
                continue
            all_rows.append({
                "symbol": binance_sym,
                "window": w,
                "window_start": str(sub.index[0].date()),
                **m,
            })
        print(f"  {binance_sym}: {n_windows} windows")

    if not all_rows:
        print("No results."); return

    df_res = pd.DataFrame(all_rows)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = ROOT / "data" / f"backtest_crypto_walkforward_{args.days}d_{ts}.parquet"
    df_res.to_parquet(out_path)
    print(f"\n[saved] {out_path}")

    # ── 윈도우별 평균 (전 종목)
    print(f"\n{'='*100}")
    print(f"윈도우별 평균 (전 종목) — 시간 순")
    print(f"{'='*100}")
    print(f"{'window':>6} {'start':>12} {'avg_ret%':>9} {'avg_Sharpe':>11} {'avg_DD%':>9} {'avg_bnh%':>9} {'우세regime':>13} {'+종목/총':>9}")
    print("-" * 100)
    wg = df_res.groupby("window")
    window_summary = []
    for w, g in wg:
        avg_ret = g["window_return_pct"].mean()
        avg_sharpe = g["sharpe"].mean()
        avg_dd = g["max_dd_pct"].mean()
        avg_bnh = g["bnh_pct"].mean()
        # 우세 regime
        regime_mode = g["regime"].mode().iloc[0] if not g["regime"].mode().empty else "?"
        n_pos = int((g["window_return_pct"] > 0).sum())
        n_tot = len(g)
        start = g["window_start"].iloc[0]
        print(f"{w:>6} {start:>12} {avg_ret:>+9.2f} {avg_sharpe:>+11.2f} {avg_dd:>+9.2f} {avg_bnh:>+9.2f} {regime_mode:>13} {n_pos:>4}/{n_tot:<4}")
        window_summary.append({
            "window": int(w), "start": start, "avg_ret": avg_ret,
            "avg_sharpe": avg_sharpe, "avg_dd": avg_dd, "avg_bnh": avg_bnh,
            "regime": regime_mode, "n_positive": n_pos, "n_total": n_tot,
        })

    # ── regime 별 집계 (핵심 — 추세 의존인가?)
    print(f"\n{'='*80}")
    print(f"시장 국면별 전략 성과 — '추세 의존 vs 꾸준' 판별")
    print(f"{'='*80}")
    print(f"{'regime':>14} {'n_windows':>10} {'avg_ret%':>10} {'avg_Sharpe':>11} {'승률%':>8}")
    print("-" * 80)
    rg = df_res.groupby("regime")
    regime_summary = []
    for reg in ["STRONG_TREND", "WEAK_TREND", "RANGE"]:
        if reg not in rg.groups:
            continue
        g = rg.get_group(reg)
        avg_ret = g["window_return_pct"].mean()
        avg_sharpe = g["sharpe"].mean()
        win_rate = float((g["window_return_pct"] > 0).mean() * 100)
        print(f"{reg:>14} {len(g):>10} {avg_ret:>+10.2f} {avg_sharpe:>+11.2f} {win_rate:>8.1f}")
        regime_summary.append({
            "regime": reg, "n": len(g), "avg_ret": avg_ret,
            "avg_sharpe": avg_sharpe, "win_rate": win_rate,
        })

    # ── 전체 통계
    print(f"\n{'='*80}")
    print(f"전체 윈도우 통계 (종목 × 윈도우 = {len(df_res)} 표본)")
    print(f"{'='*80}")
    overall_win = float((df_res["window_return_pct"] > 0).mean() * 100)
    print(f"  +수익 윈도우 비율 (승률) : {overall_win:.1f}%")
    print(f"  평균 윈도우 수익률        : {df_res['window_return_pct'].mean():+.2f}%")
    print(f"  중앙값 윈도우 수익률      : {df_res['window_return_pct'].median():+.2f}%")
    print(f"  최고 윈도우              : {df_res['window_return_pct'].max():+.2f}%")
    print(f"  최악 윈도우              : {df_res['window_return_pct'].min():+.2f}%")
    print(f"  윈도우 수익 표준편차      : {df_res['window_return_pct'].std():.2f}%")

    # JSON
    summary = {
        "params": {"days": args.days, "window_days": args.window_days,
                   "trail_pct": args.trail_pct, "fast": args.fast, "slow": args.slow,
                   "symbols": [s[1] for s in SYMBOLS]},
        "window_summary": window_summary,
        "regime_summary": regime_summary,
        "overall": {
            "win_rate": overall_win,
            "mean_window_ret": float(df_res["window_return_pct"].mean()),
            "median_window_ret": float(df_res["window_return_pct"].median()),
            "std_window_ret": float(df_res["window_return_pct"].std()),
        },
    }
    summary_path = ROOT / "data" / f"backtest_crypto_walkforward_{args.days}d_{ts}_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n[saved] {summary_path}")


if __name__ == "__main__":
    main()
