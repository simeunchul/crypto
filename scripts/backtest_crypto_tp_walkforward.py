"""Walk-forward 검증 — tiered_trail+3% vs baseline_trail2 robustness 테스트.

질문: "365d in-sample 에서 tiered+3% 가 baseline trail2 보다 sharpe +0.21 좋았다.
       이게 시간/국면에 걸쳐 꾸준한 개선인가, 일부 구간 오버핏인가?"

방법 (기존 walk-forward 패턴 그대로):
  - 18종 × 4h × 365일 → 30일 비중첩 윈도우 × 12개 = 종목당 12 window
  - 각 윈도우에서 baseline vs tiered+3% 각각 성과 측정
  - 시장 국면 분류 (buy&hold |수익률| 으로 STRONG_TREND / WEAK_TREND / RANGE)

판정:
  - 윈도우별 baseline 대비 tiered+3% Sharpe 차이 → 분포(승률, 평균, 분산)
  - 시장 국면별로 어느 룰이 이기는지 → regime dependence 체크
  - 종목별 일관성

해석:
  - tiered+3% 승률 > 60% + 모든 regime 에서 positive delta → robust, 채택 가능
  - 한 regime 에만 강함 → regime-dependent, 운영 시 주의
  - 50% 근처 / 분산 큼 → 오버핏 의심, 채택 보류
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

# 동일 종목 set
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


# ─── overlays (TP sweep 과 동일) ─────────────────────────────────

def overlay_trail_only(df, base, trail=0.02):
    out = np.zeros(len(df), dtype=float)
    state = 0; entry = None; hw = lw = None; blocked = 0
    closes = df["close"].values
    bps = base.fillna(0).astype(int).values
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
    ap.add_argument("--tighten-at", type=float, default=0.03,
                    help="tiered trail tighten threshold (+N% 이익 시 trail 2->1%)")
    args = ap.parse_args()

    print(f"=== TP Walk-Forward — baseline vs tiered+{args.tighten_at*100:.0f}% ===")
    print(f"    {args.days}d, {args.window_days}d 비중첩 윈도우, 4h+{args.fast}/{args.slow}")

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

        pos_base = overlay_trail_only(df, base_pos, trail=0.02)
        pos_tier = overlay_tiered_trail(df, base_pos,
                                         trail_loose=0.02, trail_tight=0.01,
                                         tighten_at=args.tighten_at)
        out_base = backtest(df, pos_base, cfg)
        out_tier = backtest(df, pos_tier, cfg)

        n = len(out_base)
        n_windows = n // window_bars
        for w in range(n_windows):
            lo = w * window_bars
            hi = lo + window_bars
            sb = out_base.iloc[lo:hi]
            st = out_tier.iloc[lo:hi]
            if len(sb) < 2: continue
            bnh = float((sb["close"].iloc[-1] / sb["close"].iloc[0] - 1) * 100)
            mb = window_metrics(sb); mt = window_metrics(st)
            if not mb or not mt: continue
            rows.append({
                "symbol": binance_sym,
                "window": w,
                "window_start": str(sb.index[0].date()),
                "bnh_pct": bnh,
                "regime": classify_regime(bnh),
                "base_return_pct": mb["return_pct"],
                "base_sharpe": mb["sharpe"],
                "base_dd_pct": mb["max_dd_pct"],
                "tier_return_pct": mt["return_pct"],
                "tier_sharpe": mt["sharpe"],
                "tier_dd_pct": mt["max_dd_pct"],
                "delta_return_pct": mt["return_pct"] - mb["return_pct"],
                "delta_sharpe": mt["sharpe"] - mb["sharpe"],
                "delta_dd_pct": mt["max_dd_pct"] - mb["max_dd_pct"],
                "tier_wins": int(mt["sharpe"] > mb["sharpe"]),
            })
        print(f"  {binance_sym}: {n_windows} windows")

    if not rows:
        print("NO DATA"); return

    df_res = pd.DataFrame(rows)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    parq = ROOT / "data" / f"backtest_tp_walkforward_{args.days}d_{ts}.parquet"
    df_res.to_parquet(parq)

    n = len(df_res)
    win_rate = df_res["tier_wins"].mean() * 100
    avg_delta_sharpe = df_res["delta_sharpe"].mean()
    med_delta_sharpe = df_res["delta_sharpe"].median()
    avg_delta_ret = df_res["delta_return_pct"].mean()

    print(f"\n=== 전체 윈도우 결과 (n={n}) ===")
    print(f"  tier 승률           : {win_rate:>6.1f}%  (50% 가 동전던지기)")
    print(f"  평균 Δ Sharpe       : {avg_delta_sharpe:>+6.3f}")
    print(f"  중앙 Δ Sharpe       : {med_delta_sharpe:>+6.3f}")
    print(f"  평균 Δ return       : {avg_delta_ret:>+6.2f}%p")

    # regime 별
    print(f"\n=== 시장 국면별 ===")
    print(f"  {'regime':<14s} {'n':>4s} {'tier_win%':>9s} {'Δsharpe':>9s} {'Δret%':>8s} {'Δdd%':>8s}")
    for reg in ["STRONG_TREND", "WEAK_TREND", "RANGE"]:
        g = df_res[df_res["regime"] == reg]
        if g.empty: continue
        print(f"  {reg:<14s} {len(g):>4d} {g['tier_wins'].mean()*100:>8.1f}% "
              f"{g['delta_sharpe'].mean():>+9.3f} {g['delta_return_pct'].mean():>+7.2f}%p "
              f"{g['delta_dd_pct'].mean():>+7.2f}%p")

    # 종목별
    print(f"\n=== 종목별 (Δsharpe 내림차순) ===")
    print(f"  {'symbol':<10s} {'n':>4s} {'tier_win%':>9s} {'avg_Δsh':>8s} {'avg_Δret%':>9s}")
    sym_agg = df_res.groupby("symbol").agg(
        n=("window", "count"),
        win_pct=("tier_wins", lambda x: x.mean() * 100),
        avg_delta_sharpe=("delta_sharpe", "mean"),
        avg_delta_return=("delta_return_pct", "mean"),
    ).sort_values("avg_delta_sharpe", ascending=False)
    for sym, r in sym_agg.iterrows():
        flag = "OK" if r["avg_delta_sharpe"] > 0 else "no"
        print(f"  {sym:<10s} {int(r['n']):>4d} {r['win_pct']:>8.1f}% "
              f"{r['avg_delta_sharpe']:>+8.3f} {r['avg_delta_return']:>+8.2f}%p  {flag}")

    # 시간 진행에 따른 윈도우별 평균 Δ
    print(f"\n=== 시간순 윈도우 평균 Δsharpe (오버핏 검출용) ===")
    print(f"  {'window':>6s} {'start':>12s} {'tier_win%':>9s} {'avg_Δsh':>8s} {'regime':>14s}")
    wg = df_res.groupby("window")
    for w, g in wg:
        reg_mode = g["regime"].mode().iloc[0] if not g["regime"].mode().empty else "?"
        print(f"  {w:>6d} {g['window_start'].iloc[0]:>12s} "
              f"{g['tier_wins'].mean()*100:>8.1f}% "
              f"{g['delta_sharpe'].mean():>+8.3f} {reg_mode:>14s}")

    print(f"\n[saved] {parq}")


if __name__ == "__main__":
    main()
