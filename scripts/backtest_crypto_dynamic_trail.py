"""Dynamic trail 폭 비교 backtest — 고정 vs ATR vs trend-adaptive.

질문: "trail 폭 2% 고정 = 변동성 큰 추세장에 휩쏘. 동적으로 계산하면 더 좋은가?"

비교 대상:
  fixed_2%, fixed_3%, fixed_4%, fixed_5%       — 단순 고정 폭 증가
  atr_1.5x, atr_2x, atr_3x                       — ATR × multiplier 동적
  trend_adaptive (weak2/medium3/strong5)        — 추세 강도 기반

세팅:
  - 4h × MA 12/48 × long-short
  - 18종 × 두 기간 (최근 30일 / 365일) 비교
  - cooldown_1봉 정책 (현재 운영 정책 유지)
  - 거래비용 4 bps/side

목적:
  1. 30일 (최근 손실 시기) 어떤 trail 이 가장 robust 한가
  2. 365일 결과와 일관되는가
  3. 운영 정책 바꿀 만한 명확한 winner 가 있는가
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from autotrader.backtest.crypto_strategies import (
    BacktestConfig, backtest, strat_trend, compute_atr,
)
from autotrader.data.crypto_bars import fetch_crypto_bars

SYMBOLS = [
    ("BTC/USDT:USDT","BTCUSDT"),("ETH/USDT:USDT","ETHUSDT"),
    ("SOL/USDT:USDT","SOLUSDT"),("AVAX/USDT:USDT","AVAXUSDT"),
    ("BNB/USDT:USDT","BNBUSDT"),("DOGE/USDT:USDT","DOGEUSDT"),
    ("ADA/USDT:USDT","ADAUSDT"),("XRP/USDT:USDT","XRPUSDT"),
    ("DOT/USDT:USDT","DOTUSDT"),("LINK/USDT:USDT","LINKUSDT"),
    ("LTC/USDT:USDT","LTCUSDT"),("BCH/USDT:USDT","BCHUSDT"),
    ("ARB/USDT:USDT","ARBUSDT"),("OP/USDT:USDT","OPUSDT"),
    ("SUI/USDT:USDT","SUIUSDT"),("INJ/USDT:USDT","INJUSDT"),
    ("NEAR/USDT:USDT","NEARUSDT"),("ATOM/USDT:USDT","ATOMUSDT"),
]

BARS_PER_YEAR_4H = 2190
BARS_PER_DAY_4H = 6


# ──────────────────────────────────────────── overlays (모두 cooldown_1봉)

def overlay_fixed_cooldown(df, base, trail_pct, cooldown_bars=1):
    """고정 trail 폭 + cooldown_1봉 (현재 운영 정책 형식)."""
    out = np.zeros(len(df), dtype=float)
    state=0; entry=None; hw=lw=None; blocked=0; stop_bar=-1
    closes=df["close"].values; bps=base.fillna(0).astype(int).values
    for i in range(len(df)):
        p=float(closes[i]); bp=int(bps[i])
        stopped=False
        if state!=0 and entry is not None:
            if state==1:
                hw=max(hw or p, p)
                if p < hw*(1-trail_pct): stopped=True
            else:
                lw=min(lw or p, p)
                if p > lw*(1+trail_pct): stopped=True
        if stopped:
            blocked=state; stop_bar=i; state=0; entry=None; hw=lw=None
        if blocked!=0 and i-stop_bar >= cooldown_bars: blocked=0
        if state==0 and bp!=0 and bp!=blocked:
            state=bp; entry=p
            hw=p if bp==1 else None; lw=p if bp==-1 else None
        out[i]=state
    return pd.Series(out, index=df.index)


def overlay_atr_cooldown(df, base, atr_window=14, atr_mult=2.0, cooldown_bars=1):
    """ATR × multiplier 동적 trail + cooldown_1봉."""
    atr = compute_atr(df, atr_window).fillna(0).values
    out = np.zeros(len(df), dtype=float)
    state=0; entry=None; hw=lw=None; blocked=0; stop_bar=-1
    closes=df["close"].values; bps=base.fillna(0).astype(int).values
    for i in range(len(df)):
        p=float(closes[i]); bp=int(bps[i]); a=float(atr[i])
        stopped=False
        if state!=0 and entry is not None and a > 0:
            if state==1:
                hw=max(hw or p, p)
                if p < hw - atr_mult*a: stopped=True
            else:
                lw=min(lw or p, p)
                if p > lw + atr_mult*a: stopped=True
        if stopped:
            blocked=state; stop_bar=i; state=0; entry=None; hw=lw=None
        if blocked!=0 and i-stop_bar >= cooldown_bars: blocked=0
        if state==0 and bp!=0 and bp!=blocked:
            state=bp; entry=p
            hw=p if bp==1 else None; lw=p if bp==-1 else None
        out[i]=state
    return pd.Series(out, index=df.index)


def overlay_trend_adaptive_cooldown(df, base, cooldown_bars=1,
                                      fast_ma=12, slow_ma=48,
                                      weak_thr=0.01, strong_thr=0.03,
                                      weak_trail=0.02, medium_trail=0.03, strong_trail=0.05):
    """fast/slow MA gap 으로 추세 강도 측정 → 동적 trail 폭."""
    fast = df["close"].rolling(fast_ma).mean()
    slow = df["close"].rolling(slow_ma).mean()
    strength = (fast - slow).abs() / slow.replace(0, np.nan)
    strength = strength.fillna(0).values
    closes = df["close"].values
    bps = base.fillna(0).astype(int).values
    out = np.zeros(len(df), dtype=float)
    state=0; entry=None; hw=lw=None; blocked=0; stop_bar=-1
    for i in range(len(df)):
        p=float(closes[i]); bp=int(bps[i])
        s = float(strength[i])
        trail = strong_trail if s >= strong_thr else (medium_trail if s >= weak_thr else weak_trail)
        stopped=False
        if state!=0 and entry is not None:
            if state==1:
                hw=max(hw or p, p)
                if p < hw*(1-trail): stopped=True
            else:
                lw=min(lw or p, p)
                if p > lw*(1+trail): stopped=True
        if stopped:
            blocked=state; stop_bar=i; state=0; entry=None; hw=lw=None
        if blocked!=0 and i-stop_bar >= cooldown_bars: blocked=0
        if state==0 and bp!=0 and bp!=blocked:
            state=bp; entry=p
            hw=p if bp==1 else None; lw=p if bp==-1 else None
        out[i]=state
    return pd.Series(out, index=df.index)


# ──────────────────────────────────────────── 정책 목록

POLICIES = [
    # 고정 폭
    ("fixed_2%",   "fixed", dict(trail_pct=0.02)),
    ("fixed_3%",   "fixed", dict(trail_pct=0.03)),
    ("fixed_4%",   "fixed", dict(trail_pct=0.04)),
    ("fixed_5%",   "fixed", dict(trail_pct=0.05)),
    # ATR 동적
    ("atr_1.5x",   "atr", dict(atr_mult=1.5)),
    ("atr_2x",     "atr", dict(atr_mult=2.0)),
    ("atr_3x",     "atr", dict(atr_mult=3.0)),
    # trend adaptive
    ("trend_adaptive", "adaptive", dict()),
]


def overlay(name, kind, opts, df, base):
    if kind == "fixed":
        return overlay_fixed_cooldown(df, base, **opts)
    elif kind == "atr":
        return overlay_atr_cooldown(df, base, **opts)
    elif kind == "adaptive":
        return overlay_trend_adaptive_cooldown(df, base, **opts)


def window_metrics(out):
    rets = out["net_ret"]
    if len(out) < 2 or rets.std() == 0: return None
    eq = (1 + rets).cumprod()
    return {
        "return": float(eq.iloc[-1] - 1) * 100,
        "sharpe": float(rets.mean() / rets.std() * np.sqrt(BARS_PER_YEAR_4H)),
        "max_dd": float(((eq / eq.cummax()) - 1).min()) * 100,
        "flips": int((out["position"].diff().abs() > 0).sum()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30,
                     help="검증 기간 (default 30 = 최근 손실 시기)")
    ap.add_argument("--cost-bps", type=float, default=4.0)
    ap.add_argument("--fast", type=int, default=12)
    ap.add_argument("--slow", type=int, default=48)
    args = ap.parse_args()

    print(f"=== Dynamic Trail 비교 ({args.days}d, cost {args.cost_bps} bps) ===")
    cfg = BacktestConfig(fast_ma=args.fast, slow_ma=args.slow,
                          cost_bps_per_side=args.cost_bps, bars_per_year=BARS_PER_YEAR_4H)

    # 각 정책별 집계 (18종 평균)
    rows = []
    for name, kind, opts in POLICIES:
        sym_metrics = []
        for ccxt, sym in SYMBOLS:
            try:
                df = fetch_crypto_bars(ccxt, timeframe="4h", days=args.days + 30)
            except Exception:
                continue
            if df is None or df.empty or len(df) < args.slow + 60:
                continue
            base_pos = strat_trend(df, cfg)
            # 최근 N일 만 슬라이스
            n_bars = args.days * BARS_PER_DAY_4H
            recent_df = df.iloc[-n_bars:].reset_index(drop=True)
            recent_base = base_pos.iloc[-n_bars:].reset_index(drop=True)
            try:
                pos = overlay(name, kind, opts, recent_df, recent_base)
            except Exception as e:
                print(f"  [{sym}/{name}] overlay fail: {e}")
                continue
            out = backtest(recent_df, pos, cfg)
            m = window_metrics(out)
            if m: sym_metrics.append((sym, m))
        if not sym_metrics:
            continue
        avg_sh = np.mean([m["sharpe"] for _, m in sym_metrics])
        med_sh = np.median([m["sharpe"] for _, m in sym_metrics])
        avg_ret = np.mean([m["return"] for _, m in sym_metrics])
        avg_dd = np.mean([m["max_dd"] for _, m in sym_metrics])
        avg_flips = np.mean([m["flips"] for _, m in sym_metrics])
        positive_syms = sum(1 for _, m in sym_metrics if m["return"] > 0)
        rows.append(dict(
            policy=name, n_symbols=len(sym_metrics),
            mean_sharpe=avg_sh, median_sharpe=med_sh,
            mean_return=avg_ret, mean_dd=avg_dd, mean_flips=avg_flips,
            positive_symbols=positive_syms,
        ))

    df_res = pd.DataFrame(rows).sort_values("mean_sharpe", ascending=False)
    print()
    print(f"{'정책':<18s} {'n':>4s} {'mean_sh':>9s} {'med_sh':>9s} {'mean_ret%':>10s} {'mean_dd%':>9s} {'+syms':>6s} {'flips':>7s}")
    for _, r in df_res.iterrows():
        print(f"{r['policy']:<18s} {int(r['n_symbols']):>4d} "
              f"{r['mean_sharpe']:>+9.3f} {r['median_sharpe']:>+9.3f} "
              f"{r['mean_return']:>+9.2f} {r['mean_dd']:>+9.2f} "
              f"{int(r['positive_symbols']):>4d}/{int(r['n_symbols'])} {r['mean_flips']:>7.1f}")

    # winner 기준 baseline (fixed_2%) 대비
    baseline = df_res[df_res["policy"] == "fixed_2%"].iloc[0]
    print()
    print(f"=== fixed_2% (현재 운영) 대비 ===")
    for _, r in df_res.iterrows():
        delta_sh = r["mean_sharpe"] - baseline["mean_sharpe"]
        delta_ret = r["mean_return"] - baseline["mean_return"]
        marker = "  win" if delta_sh > 0.1 else ("  loss" if delta_sh < -0.1 else "")
        print(f"  {r['policy']:<18s} Δsh={delta_sh:>+6.3f}  Δret={delta_ret:>+7.2f}%p{marker}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = ROOT / "data" / f"backtest_dynamic_trail_{args.days}d_{ts}.parquet"
    df_res.to_parquet(out_path)
    print(f"\n[saved] {out_path}")


if __name__ == "__main__":
    main()
