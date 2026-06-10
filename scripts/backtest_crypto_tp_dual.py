"""세 가지 청산 전략 head-to-head 비교 — baseline vs B (고정 14종) vs C (regime 동적).

비교:
  A. baseline   — 전 종목 trail 2% (현행 운영봇)
  B. fixed_14   — 14종 (winners) tiered+3%, 4종 (ADA/SUI/NEAR/LINK) baseline
  C. regime_dynamic — 종목별 30일 |bnh%| 로 진입 시점 regime 판정.
                       |bnh| >= 15% (STRONG_TREND) → baseline
                       |bnh|  < 15% (WEAK_TREND, RANGE) → tiered+3%
                       포지션 중간 regime 전환은 안 함 (진입 시점에 한 번만 결정)

세팅: 4h × MA 12/48, long-short, 18종, 365d, 30일 비중첩 윈도우 11개 = 198 표본.
출력: 윈도우별·국면별·종목별로 세 전략을 줄 세움.
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

SYMBOLS = [
    ("BTC/USDT:USDT",  "BTCUSDT"),
    ("ETH/USDT:USDT",  "ETHUSDT"),
    ("SOL/USDT:USDT",  "SOLUSDT"),
    ("AVAX/USDT:USDT", "AVAXUSDT"),
    ("BNB/USDT:USDT",  "BNBUSDT"),
    ("DOGE/USDT:USDT", "DOGEUSDT"),
    ("ADA/USDT:USDT",  "ADAUSDT"),
    ("XRP/USDT:USDT",  "XRPUSDT"),
    ("DOT/USDT:USDT",  "DOTUSDT"),
    ("LINK/USDT:USDT", "LINKUSDT"),
    ("LTC/USDT:USDT",  "LTCUSDT"),
    ("BCH/USDT:USDT",  "BCHUSDT"),
    ("ARB/USDT:USDT",  "ARBUSDT"),
    ("OP/USDT:USDT",   "OPUSDT"),
    ("SUI/USDT:USDT",  "SUIUSDT"),
    ("INJ/USDT:USDT",  "INJUSDT"),
    ("NEAR/USDT:USDT", "NEARUSDT"),
    ("ATOM/USDT:USDT", "ATOMUSDT"),
]

BARS_PER_YEAR_4H = 2190
BARS_PER_DAY_4H = 6

# 옵션 B 에서 baseline 유지할 종목 (walk-forward 에서 tiered 가 손해였던 4종)
BASELINE_ONLY_SYMBOLS = {"ADAUSDT", "SUIUSDT", "NEARUSDT", "LINKUSDT"}

# 옵션 C regime 판정 hyperparam
REGIME_LOOKBACK_BARS = 30 * BARS_PER_DAY_4H   # 30일
REGIME_STRONG_THRESHOLD = 0.15                # |bnh| 15%


def overlay_trail_only(df, base, trail=0.02):
    """A. baseline."""
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
            blocked = state
            state = 0; entry = None; hw = lw = None
        if state == 0 and bp != 0 and bp != blocked:
            state = bp; entry = p
            hw = p if bp == 1 else None
            lw = p if bp == -1 else None
            blocked = 0
        elif state == 0 and blocked != 0 and bp != blocked and bp != 0:
            blocked = 0
        out[i] = state
    return pd.Series(out, index=df.index)


def overlay_tiered_trail(df, base, trail_loose=0.02, trail_tight=0.01,
                          tighten_at=0.03):
    """B 14종에 적용 / C 의 'tiered 모드' 동일."""
    out = np.zeros(len(df), dtype=float)
    state = 0; entry = None; hw = lw = None; blocked = 0; tightened = False
    closes = df["close"].values; bps = base.fillna(0).astype(int).values
    for i in range(len(df)):
        p = float(closes[i]); bp = int(bps[i])
        stopped = False
        if state != 0 and entry is not None:
            profit_pct = (p - entry) / entry * state
            if profit_pct >= tighten_at: tightened = True
            trail = trail_tight if tightened else trail_loose
            if state == 1:
                hw = max(hw or p, p)
                if p < hw * (1 - trail): stopped = True
            else:
                lw = min(lw or p, p)
                if p > lw * (1 + trail): stopped = True
        if stopped:
            blocked = state
            state = 0; entry = None; hw = lw = None; tightened = False
        if state == 0 and bp != 0 and bp != blocked:
            state = bp; entry = p
            hw = p if bp == 1 else None
            lw = p if bp == -1 else None
            blocked = 0
        elif state == 0 and blocked != 0 and bp != blocked and bp != 0:
            blocked = 0
        out[i] = state
    return pd.Series(out, index=df.index)


def overlay_regime_dynamic(df, base,
                            lookback_bars=REGIME_LOOKBACK_BARS,
                            strong_thr=REGIME_STRONG_THRESHOLD,
                            trail_loose=0.02, trail_tight=0.01,
                            tighten_at=0.03):
    """C. 진입 시점에 종목 자체의 30일 |bnh%| 로 regime 판정.

    |bnh| >= strong_thr (STRONG_TREND) → baseline (trail 2%, tiered 비활성)
    그 외 (WEAK_TREND, RANGE)           → tiered (trail 2% → 1% after +3%)

    포지션 중간 regime 변화는 무시 (진입 시점 결정 고정).
    """
    out = np.zeros(len(df), dtype=float)
    closes = df["close"].values
    bps = base.fillna(0).astype(int).values
    n = len(df)

    state = 0; entry = None; hw = lw = None; blocked = 0
    tightened = False
    use_tiered = False  # 현재 포지션의 모드 (진입 시 결정)

    for i in range(n):
        p = float(closes[i]); bp = int(bps[i])
        stopped = False
        if state != 0 and entry is not None:
            if use_tiered:
                profit_pct = (p - entry) / entry * state
                if profit_pct >= tighten_at: tightened = True
                trail = trail_tight if tightened else trail_loose
            else:
                trail = trail_loose   # baseline 모드
            if state == 1:
                hw = max(hw or p, p)
                if p < hw * (1 - trail): stopped = True
            else:
                lw = min(lw or p, p)
                if p > lw * (1 + trail): stopped = True
        if stopped:
            blocked = state
            state = 0; entry = None; hw = lw = None
            tightened = False; use_tiered = False

        if state == 0 and bp != 0 and bp != blocked:
            # 진입 시점에 regime 판정
            if i >= lookback_bars:
                bnh = (closes[i] / closes[i - lookback_bars]) - 1.0
                use_tiered = (abs(bnh) < strong_thr)
            else:
                use_tiered = False   # warmup: 보수적으로 baseline
            state = bp; entry = p
            hw = p if bp == 1 else None
            lw = p if bp == -1 else None
            blocked = 0
            tightened = False
        elif state == 0 and blocked != 0 and bp != blocked and bp != 0:
            blocked = 0

        out[i] = state
    return pd.Series(out, index=df.index)


def classify_regime(bnh_pct: float) -> str:
    a = abs(bnh_pct)
    if a >= 15: return "STRONG_TREND"
    if a >= 6:  return "WEAK_TREND"
    return "RANGE"


def window_metrics(sub: pd.DataFrame) -> dict:
    rets = sub["net_ret"]
    if len(sub) < 2 or rets.std() == 0:
        return {}
    eq = (1 + rets).cumprod()
    total = float(eq.iloc[-1] - 1)
    sharpe = float(rets.mean() / rets.std() * np.sqrt(BARS_PER_YEAR_4H))
    dd = float(((eq / eq.cummax()) - 1).min())
    flips = int((sub["position"].diff().abs() > 0).sum())
    return {
        "return_pct": total * 100,
        "sharpe": sharpe,
        "max_dd_pct": dd * 100,
        "n_flips": flips,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--fast", type=int, default=12)
    ap.add_argument("--slow", type=int, default=48)
    args = ap.parse_args()

    print(f"=== Dual TP head-to-head — A(baseline) vs B(14종 tiered) vs C(regime 동적) ===")
    print(f"    {args.days}d, {args.window_days}d 윈도우, 4h+{args.fast}/{args.slow}")

    cfg = BacktestConfig(fast_ma=args.fast, slow_ma=args.slow,
                          cost_bps_per_side=4.0,
                          bars_per_year=BARS_PER_YEAR_4H)
    window_bars = args.window_days * BARS_PER_DAY_4H

    rows: list[dict] = []
    for ccxt_sym, binance_sym in SYMBOLS:
        try:
            df = fetch_crypto_bars(ccxt_sym, timeframe="4h", days=args.days)
        except Exception as e:
            print(f"  {binance_sym}: fetch fail {e}"); continue
        if df.empty or len(df) < args.slow + window_bars:
            print(f"  {binance_sym}: insufficient ({len(df)} bars)"); continue

        base_pos = strat_trend(df, cfg)

        # A. baseline
        pos_A = overlay_trail_only(df, base_pos, trail=0.02)
        # B. fixed: 14종은 tiered, 4종(ADA/SUI/NEAR/LINK)은 baseline
        if binance_sym in BASELINE_ONLY_SYMBOLS:
            pos_B = overlay_trail_only(df, base_pos, trail=0.02)
            b_mode = "baseline"
        else:
            pos_B = overlay_tiered_trail(df, base_pos)
            b_mode = "tiered"
        # C. regime dynamic
        pos_C = overlay_regime_dynamic(df, base_pos)

        out_A = backtest(df, pos_A, cfg)
        out_B = backtest(df, pos_B, cfg)
        out_C = backtest(df, pos_C, cfg)

        n = len(out_A)
        n_windows = n // window_bars
        for w in range(n_windows):
            lo = w * window_bars; hi = lo + window_bars
            sA = out_A.iloc[lo:hi]; sB = out_B.iloc[lo:hi]; sC = out_C.iloc[lo:hi]
            if len(sA) < 2: continue
            bnh = float((sA["close"].iloc[-1] / sA["close"].iloc[0] - 1) * 100)
            mA = window_metrics(sA); mB = window_metrics(sB); mC = window_metrics(sC)
            if not (mA and mB and mC): continue
            rows.append({
                "symbol": binance_sym,
                "window": w,
                "window_start": str(sA.index[0].date()),
                "bnh_pct": bnh,
                "regime": classify_regime(bnh),
                "b_mode": b_mode,
                "A_sharpe": mA["sharpe"], "A_return": mA["return_pct"], "A_dd": mA["max_dd_pct"],
                "B_sharpe": mB["sharpe"], "B_return": mB["return_pct"], "B_dd": mB["max_dd_pct"],
                "C_sharpe": mC["sharpe"], "C_return": mC["return_pct"], "C_dd": mC["max_dd_pct"],
            })
        print(f"  {binance_sym}: {n_windows} windows (B mode={b_mode})")

    if not rows:
        print("NO DATA"); return

    df_res = pd.DataFrame(rows)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    parq = ROOT / "data" / f"backtest_tp_dual_{args.days}d_{ts}.parquet"
    df_res.to_parquet(parq)

    n = len(df_res)
    print(f"\n=== 전체 평균 (n={n} 윈도우) ===")
    print(f"  {'strategy':<22s} {'mean_sh':>8s} {'med_sh':>8s} {'mean_ret%':>10s} {'mean_dd%':>9s}")
    for label, sh_col, ret_col, dd_col in [
        ("A. baseline (현행)",   "A_sharpe", "A_return", "A_dd"),
        ("B. 14종 tiered 고정",   "B_sharpe", "B_return", "B_dd"),
        ("C. regime 동적 (per-coin)",  "C_sharpe", "C_return", "C_dd"),
    ]:
        print(f"  {label:<22s} {df_res[sh_col].mean():>+8.3f} {df_res[sh_col].median():>+8.3f} "
              f"{df_res[ret_col].mean():>+9.2f} {df_res[dd_col].mean():>+8.2f}")

    # head-to-head 승률 (A vs B, A vs C, B vs C)
    print(f"\n=== Head-to-head 승률 (sharpe 기준, 동률 무시) ===")
    bA_v_B = (df_res["B_sharpe"] > df_res["A_sharpe"]).mean() * 100
    bA_v_C = (df_res["C_sharpe"] > df_res["A_sharpe"]).mean() * 100
    bB_v_C = (df_res["C_sharpe"] > df_res["B_sharpe"]).mean() * 100
    print(f"  B beats A : {bA_v_B:>5.1f}%   ({(df_res['B_sharpe']>df_res['A_sharpe']).sum()} / {n})")
    print(f"  C beats A : {bA_v_C:>5.1f}%   ({(df_res['C_sharpe']>df_res['A_sharpe']).sum()} / {n})")
    print(f"  C beats B : {bB_v_C:>5.1f}%   ({(df_res['C_sharpe']>df_res['B_sharpe']).sum()} / {n})")

    print(f"\n=== regime 별 ===")
    print(f"  {'regime':<14s} {'n':>4s} {'A_sh':>7s} {'B_sh':>7s} {'C_sh':>7s} {'ΔB-A':>7s} {'ΔC-A':>7s} {'ΔC-B':>7s}")
    for reg in ["STRONG_TREND", "WEAK_TREND", "RANGE"]:
        g = df_res[df_res["regime"] == reg]
        if g.empty: continue
        A, B, C = g["A_sharpe"].mean(), g["B_sharpe"].mean(), g["C_sharpe"].mean()
        print(f"  {reg:<14s} {len(g):>4d} {A:>+7.3f} {B:>+7.3f} {C:>+7.3f} "
              f"{B-A:>+7.3f} {C-A:>+7.3f} {C-B:>+7.3f}")

    print(f"\n=== 종목별 (mean Sharpe 비교) ===")
    print(f"  {'symbol':<10s} {'B_mode':<10s} {'A':>7s} {'B':>7s} {'C':>7s} {'ΔB-A':>7s} {'ΔC-A':>7s} {'ΔC-B':>7s} winner")
    sym_agg = df_res.groupby(["symbol", "b_mode"]).agg(
        A=("A_sharpe", "mean"), B=("B_sharpe", "mean"), C=("C_sharpe", "mean"),
    ).reset_index().set_index("symbol")
    sym_agg["best"] = sym_agg[["A", "B", "C"]].idxmax(axis=1)
    sym_agg = sym_agg.sort_values("C", ascending=False)
    for sym, r in sym_agg.iterrows():
        print(f"  {sym:<10s} {r['b_mode']:<10s} "
              f"{r['A']:>+7.3f} {r['B']:>+7.3f} {r['C']:>+7.3f} "
              f"{r['B']-r['A']:>+7.3f} {r['C']-r['A']:>+7.3f} {r['C']-r['B']:>+7.3f}   {r['best']}")

    print(f"\n[saved] {parq}")


if __name__ == "__main__":
    main()
