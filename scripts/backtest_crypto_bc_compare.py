"""B (종목 수 축소) + C (regime 필터) 비교 backtest.

질문:
  B. 18종 균등 분할 vs 5종 winners 집중 — sharpe / drawdown 어느 쪽이 나은가?
  C. signal 진입 시 30일 buy&hold |수익률| 으로 RANGE 국면이면 진입 건너뛰면 휩쏘 비용 감소하는가?

비교:
  Baseline_18  : 18종, 4h+12/48+trail2 long-short (현재 운영 구성)
  B_5종        : BTC/ETH/SOL/AVAX/BNB 만, 동일 룰
  C_regime     : 18종 + |bnh|<6% 시 진입 차단

세팅: 365일, 30일 비중첩 윈도우 11개.
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

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from autotrader.backtest.crypto_strategies import (
    BacktestConfig, backtest, strat_trend,
)
from autotrader.data.crypto_bars import fetch_crypto_bars

ALT18 = [
    ("BTC/USDT:USDT",  "BTCUSDT"),  ("ETH/USDT:USDT",  "ETHUSDT"),
    ("SOL/USDT:USDT",  "SOLUSDT"),  ("AVAX/USDT:USDT", "AVAXUSDT"),
    ("BNB/USDT:USDT",  "BNBUSDT"),  ("DOGE/USDT:USDT", "DOGEUSDT"),
    ("ADA/USDT:USDT",  "ADAUSDT"),  ("XRP/USDT:USDT",  "XRPUSDT"),
    ("DOT/USDT:USDT",  "DOTUSDT"),  ("LINK/USDT:USDT", "LINKUSDT"),
    ("LTC/USDT:USDT",  "LTCUSDT"),  ("BCH/USDT:USDT",  "BCHUSDT"),
    ("ARB/USDT:USDT",  "ARBUSDT"),  ("OP/USDT:USDT",   "OPUSDT"),
    ("SUI/USDT:USDT",  "SUIUSDT"),  ("INJ/USDT:USDT",  "INJUSDT"),
    ("NEAR/USDT:USDT", "NEARUSDT"), ("ATOM/USDT:USDT", "ATOMUSDT"),
]

WINNERS_5 = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "BNBUSDT"}

BARS_PER_YEAR_4H = 2190
BARS_PER_DAY_4H = 6
REGIME_LOOKBACK = 30 * BARS_PER_DAY_4H   # 30일
REGIME_RANGE_THRESHOLD = 0.06            # |bnh| < 6% → RANGE → 진입 차단


def overlay_trail_only(df, base, trail=0.02):
    """기준선 — trail-only, stopped_until_signal_change 유지."""
    out = np.zeros(len(df), dtype=float)
    state = 0; entry = None; hw = lw = None; blocked = 0
    closes = df["close"].values; bps = base.fillna(0).astype(int).values
    for i in range(len(df)):
        p = float(closes[i]); bp = int(bps[i])
        stopped = False
        if state != 0 and entry is not None:
            if state == 1:
                hw = max(hw or p, p)
                if p < hw * (1 - trail): stopped = True
            else:
                lw = min(lw or p, p)
                if p > lw * (1 + trail): stopped = True
        if stopped:
            blocked = state; state = 0; entry = None; hw = lw = None
        if state == 0 and bp != 0 and bp != blocked:
            state = bp; entry = p
            hw = p if bp == 1 else None
            lw = p if bp == -1 else None
            blocked = 0
        elif state == 0 and blocked != 0 and bp != blocked and bp != 0:
            blocked = 0
        out[i] = state
    return pd.Series(out, index=df.index)


def overlay_regime_filter(df, base, trail=0.02,
                           lookback=REGIME_LOOKBACK,
                           range_thr=REGIME_RANGE_THRESHOLD):
    """C — RANGE 국면 진입 차단. trail 청산은 그대로."""
    out = np.zeros(len(df), dtype=float)
    state = 0; entry = None; hw = lw = None; blocked = 0
    closes = df["close"].values; bps = base.fillna(0).astype(int).values
    for i in range(len(df)):
        p = float(closes[i]); bp = int(bps[i])
        stopped = False
        if state != 0 and entry is not None:
            if state == 1:
                hw = max(hw or p, p)
                if p < hw * (1 - trail): stopped = True
            else:
                lw = min(lw or p, p)
                if p > lw * (1 + trail): stopped = True
        if stopped:
            blocked = state; state = 0; entry = None; hw = lw = None
        if state == 0 and bp != 0 and bp != blocked:
            # regime 체크 — RANGE 면 차단
            in_range = False
            if i >= lookback:
                bnh = (closes[i] / closes[i - lookback]) - 1.0
                in_range = abs(bnh) < range_thr
            if not in_range:
                state = bp; entry = p
                hw = p if bp == 1 else None
                lw = p if bp == -1 else None
                blocked = 0
            # in_range 면 state 0 유지 (진입 건너뜀)
        elif state == 0 and blocked != 0 and bp != blocked and bp != 0:
            blocked = 0
        out[i] = state
    return pd.Series(out, index=df.index)


def window_metrics(sub):
    rets = sub["net_ret"]
    if len(sub) < 2 or rets.std() == 0:
        return None
    eq = (1 + rets).cumprod()
    return {
        "return": float(eq.iloc[-1] - 1) * 100,
        "sharpe": float(rets.mean() / rets.std() * np.sqrt(BARS_PER_YEAR_4H)),
        "max_dd": float(((eq / eq.cummax()) - 1).min()) * 100,
        "flips": int((sub["position"].diff().abs() > 0).sum()),
    }


def run_strategy(symbols_subset, use_regime, days, window_days, fast, slow):
    """주어진 종목 set + 룰 조합으로 윈도우별 metric 계산."""
    cfg = BacktestConfig(fast_ma=fast, slow_ma=slow,
                          cost_bps_per_side=4.0, bars_per_year=BARS_PER_YEAR_4H)
    window_bars = window_days * BARS_PER_DAY_4H
    all_rows = []
    for ccxt_sym, binance_sym in ALT18:
        if binance_sym not in symbols_subset:
            continue
        try:
            df = fetch_crypto_bars(ccxt_sym, timeframe="4h", days=days)
        except Exception as e:
            print(f"    [{binance_sym}] fetch fail: {e}"); continue
        if df.empty or len(df) < slow + window_bars: continue
        base = strat_trend(df, cfg)
        pos = (overlay_regime_filter(df, base) if use_regime
                else overlay_trail_only(df, base))
        out = backtest(df, pos, cfg)
        n_windows = len(out) // window_bars
        for w in range(n_windows):
            sub = out.iloc[w*window_bars:(w+1)*window_bars]
            m = window_metrics(sub)
            if m:
                all_rows.append({"symbol": binance_sym, "window": w, **m})
    return pd.DataFrame(all_rows)


def summarize(df_res, label):
    if df_res.empty:
        return {"label": label, "n_windows": 0}
    return {
        "label": label,
        "n_symbols": df_res["symbol"].nunique(),
        "n_windows": len(df_res),
        "mean_sharpe": df_res["sharpe"].mean(),
        "median_sharpe": df_res["sharpe"].median(),
        "mean_return": df_res["return"].mean(),
        "mean_max_dd": df_res["max_dd"].mean(),
        "mean_flips": df_res["flips"].mean(),
        "positive_window_pct": (df_res.groupby(["symbol","window"])["return"].first() > 0).mean() * 100,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--fast", type=int, default=12)
    ap.add_argument("--slow", type=int, default=48)
    args = ap.parse_args()

    print(f"=== B/C compare: 종목 수 + regime 필터, {args.days}d / {args.window_days}d 윈도우 ===")

    ALL18 = {s[1] for s in ALT18}

    print(f"[A_baseline_18] 18종 trail-only ...")
    df_A = run_strategy(ALL18, use_regime=False,
                         days=args.days, window_days=args.window_days,
                         fast=args.fast, slow=args.slow)
    print(f"[B_winners_5] 5종 trail-only ...")
    df_B = run_strategy(WINNERS_5, use_regime=False,
                         days=args.days, window_days=args.window_days,
                         fast=args.fast, slow=args.slow)
    print(f"[C_regime_18] 18종 trail-only + RANGE 차단 ...")
    df_C = run_strategy(ALL18, use_regime=True,
                         days=args.days, window_days=args.window_days,
                         fast=args.fast, slow=args.slow)

    rows = [summarize(df_A, "A_baseline_18"),
            summarize(df_B, "B_winners_5"),
            summarize(df_C, "C_regime_18")]
    out_df = pd.DataFrame(rows)
    print()
    print(out_df.to_string(index=False))

    # 종목별 mean sharpe — 5종 winners 가 진짜 winner 인지 cross-check
    print()
    print("=== 종목별 mean sharpe (A_baseline 기준) ===")
    sym_sh = df_A.groupby("symbol")["sharpe"].agg(["mean", "median", "count"]).sort_values("mean", ascending=False)
    print(sym_sh.to_string())

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = ROOT / "data" / f"backtest_bc_compare_{args.days}d_{ts}.parquet"
    pd.concat([df_A.assign(strategy="A"),
               df_B.assign(strategy="B"),
               df_C.assign(strategy="C")]).to_parquet(out_path)
    print(f"\n[saved] {out_path}")


if __name__ == "__main__":
    main()
