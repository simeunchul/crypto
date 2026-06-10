"""Regime-switching backtest — ADX 기반 trend↔grid 전환.

가설 (5/29 발견):
  - 365일 활황기: 횡보 구간에서도 trend 가 1위 (+21.70% > grid +17.51%)
  - 최근 60일 비추세: 횡보 구간에선 grid 가 1위 (+5.22% > trend +0.51%)
  → 시장 국면이 답을 뒤집음. ADX 로 실시간 분기하면 양쪽 다 먹을 수 있나?

비교:
  trend-only      : long-short 4h+12/48 + trail2 (현 봇)
  grid-only       : grid_linear (z-score 비례 역포지션)
  regime-switch   : ADX>25 → trend pos, ADX<20 → grid pos, 중간 → 직전 모드 유지

구간: 365일 + 60일 (어제 60일 결과 재현 확인)
walk-forward: 30일 비중첩 윈도우로 각 모드 안정성
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


def compute_adx(df, window=14):
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / window, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / window, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / window, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / window, adjust=False).mean().fillna(0)


def strat_grid_linear(df, window=20, scale=2.0):
    ma = df["close"].rolling(window).mean()
    sd = df["close"].rolling(window).std()
    z = (df["close"] - ma) / sd.replace(0, np.nan)
    return (-z / scale).clip(-1, 1).shift(1).fillna(0)


def make_regime_switch_pos(df, trend_pos, grid_pos, adx, low_th, high_th):
    """ADX 기반 regime 합성. 중간(20~25)은 직전 모드 유지.

    Returns:
      합성 position 시계열 + regime mask (1=trend, -1=grid, 0=중간/직전)
    """
    regime = np.zeros(len(df), dtype=np.int8)
    mode = 0  # 1=trend, -1=grid
    for i in range(len(df)):
        a = adx.iloc[i]
        if a >= high_th:
            mode = 1
        elif a <= low_th:
            mode = -1
        regime[i] = mode

    pos = np.where(regime >= 0, trend_pos.values, grid_pos.values)
    # mode=0 (boot) → trend default
    return pd.Series(pos, index=df.index), pd.Series(regime, index=df.index)


def perf(out, label=""):
    rets = out["net_ret"]
    if rets.std() == 0:
        return {"ret_pct": 0.0, "sharpe": float("nan"), "dd_pct": 0.0, "trades": 0}
    eq = (1 + rets).cumprod()
    return {
        "ret_pct": float((eq.iloc[-1] - 1) * 100),
        "sharpe": float(rets.mean() / rets.std() * np.sqrt(BARS_PER_YEAR_4H)),
        "dd_pct": float(((eq / eq.cummax()) - 1).min() * 100),
        "trades": int((out["position"].diff().abs() > 0).sum()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--adx-low", type=float, default=20)
    ap.add_argument("--adx-high", type=float, default=25)
    ap.add_argument("--window-days", type=int, default=30)
    args = ap.parse_args()

    cfg = BacktestConfig(fast_ma=12, slow_ma=48, bb_window=20, bb_std=2.0,
                         cost_bps_per_side=4.0, bars_per_year=BARS_PER_YEAR_4H)

    print(f"=== Regime-switching backtest — {args.days}d ===")
    print(f"  ADX < {args.adx_low} → grid, > {args.adx_high} → trend, 중간 → 직전 모드 유지")

    rows = []
    wf_rows = []
    window_bars = args.window_days * 6   # 4h bar × 6 = 24h

    for ccxt_sym, binance_sym in SYMBOLS:
        try:
            df = fetch_crypto_bars(ccxt_sym, timeframe="4h", days=args.days)
        except Exception as e:
            print(f"  {binance_sym}: fetch fail {e}"); continue
        if df.empty or len(df) < 200:
            print(f"  {binance_sym}: insufficient"); continue

        adx = compute_adx(df, 14)

        # 3 전략 position 시계열
        trend_pos = apply_risk_overlay(df, strat_trend(df, cfg), trailing_stop_pct=0.02)
        grid_pos = strat_grid_linear(df)
        switch_pos, regime_series = make_regime_switch_pos(df, trend_pos, grid_pos, adx,
                                                           args.adx_low, args.adx_high)

        # 백테스트
        out_trend = backtest(df, trend_pos, cfg)
        out_grid = backtest(df, grid_pos, cfg)
        out_switch = backtest(df, switch_pos, cfg)

        p_t = perf(out_trend); p_g = perf(out_grid); p_s = perf(out_switch)
        rows.append({
            "symbol": binance_sym,
            "trend_ret": p_t["ret_pct"], "trend_sh": p_t["sharpe"], "trend_dd": p_t["dd_pct"], "trend_tr": p_t["trades"],
            "grid_ret": p_g["ret_pct"], "grid_sh": p_g["sharpe"], "grid_dd": p_g["dd_pct"], "grid_tr": p_g["trades"],
            "switch_ret": p_s["ret_pct"], "switch_sh": p_s["sharpe"], "switch_dd": p_s["dd_pct"], "switch_tr": p_s["trades"],
            "trend_bars_pct": float((regime_series == 1).mean() * 100),
            "grid_bars_pct": float((regime_series == -1).mean() * 100),
        })

        # walk-forward
        for w in range(len(df) // window_bars):
            lo, hi = w * window_bars, (w + 1) * window_bars
            for label, out in [("trend", out_trend), ("grid", out_grid), ("switch", out_switch)]:
                sub = out.iloc[lo:hi]
                if len(sub) < 5 or sub["net_ret"].std() == 0:
                    continue
                eq = (1 + sub["net_ret"]).cumprod()
                wf_rows.append({
                    "symbol": binance_sym, "strategy": label, "window": w,
                    "ret_pct": float((eq.iloc[-1] - 1) * 100),
                    "sharpe": float(sub["net_ret"].mean() / sub["net_ret"].std() * np.sqrt(BARS_PER_YEAR_4H)),
                })

        print(f"  {binance_sym}: trend={p_t['ret_pct']:+.1f}%  grid={p_g['ret_pct']:+.1f}%  "
              f"switch={p_s['ret_pct']:+.1f}%  (regime: T{rows[-1]['trend_bars_pct']:.0f}% / G{rows[-1]['grid_bars_pct']:.0f}%)")

    if not rows:
        print("No results."); return

    df_res = pd.DataFrame(rows)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = ROOT / "data" / f"backtest_crypto_regime_switch_{args.days}d_{ts}.parquet"
    df_res.to_parquet(out_path)
    print(f"\n[saved] {out_path}")

    # 전체 평균
    print(f"\n{'='*85}")
    print(f"전체 평균 (10종, {args.days}일)")
    print(f"{'='*85}")
    print(f"{'strategy':>14}  {'avg_ret%':>10} {'avg_Sharpe':>11} {'avg_DD%':>9} {'avg_trades':>11}")
    print("-" * 85)
    summary = []
    for label in ["trend", "grid", "switch"]:
        r = {
            "label": label,
            "ret": df_res[f"{label}_ret"].mean(),
            "sharpe": df_res[f"{label}_sh"].mean(),
            "dd": df_res[f"{label}_dd"].mean(),
            "trades": df_res[f"{label}_tr"].mean(),
        }
        summary.append(r)
        marker = " ★" if label == "switch" else ""
        print(f"{label:>14}  {r['ret']:>+10.2f} {r['sharpe']:>+11.2f} {r['dd']:>+9.2f} {r['trades']:>11.0f}{marker}")

    # walk-forward 승률
    if wf_rows:
        df_wf = pd.DataFrame(wf_rows)
        print(f"\n{'='*70}")
        print(f"walk-forward ({args.window_days}일 윈도우) — 안정성")
        print(f"{'='*70}")
        print(f"{'strategy':>10} {'n':>4} {'승률%':>7} {'avg_ret%':>10} {'avg_Sharpe':>11}")
        print("-" * 70)
        for label in ["trend", "grid", "switch"]:
            g = df_wf[df_wf["strategy"] == label]
            if g.empty: continue
            win = (g["ret_pct"] > 0).mean() * 100
            print(f"{label:>10} {len(g):>4} {win:>7.1f} {g['ret_pct'].mean():>+10.2f} {g['sharpe'].mean():>+11.2f}")

    # 결론
    s_trend = next(r for r in summary if r["label"] == "trend")
    s_switch = next(r for r in summary if r["label"] == "switch")
    gap = s_switch["ret"] - s_trend["ret"]
    print(f"\n{'='*70}")
    print(f"결론 — regime-switching 이 trend-only 보다 나은가?")
    print(f"{'='*70}")
    print(f"  trend-only:    {s_trend['ret']:+.2f}%  (Sharpe {s_trend['sharpe']:+.2f})")
    print(f"  regime-switch: {s_switch['ret']:+.2f}%  (Sharpe {s_switch['sharpe']:+.2f})")
    print(f"  차이:          {gap:+.2f}%p", "  ✅ switch 우세" if gap > 0 else "  ❌ trend-only 가 나음")

    summary_path = ROOT / "data" / f"backtest_crypto_regime_switch_{args.days}d_{ts}_summary.json"
    summary_path.write_text(json.dumps({"params": vars(args), "summary": summary},
                                       indent=2, default=str), encoding="utf-8")
    print(f"\n[saved] {summary_path}")


if __name__ == "__main__":
    main()
