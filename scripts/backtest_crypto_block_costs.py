"""차단 정책 × 거래비용 sensitivity backtest.

이전 결과가 너무 좋게 나온 게 비용 과소평가 때문인지 확인.
거래 비용을 4 bps → 20 bps/side 까지 sweep 하면서 baseline vs cooldown_1봉 비교.

비용 가정 (참고):
  4 bps  : 원래 사용 (낮음, maker 위주 가정)
  7 bps  : Binance Futures taker (5bps) + 약간 slippage
  10 bps : taker + 5 bps slippage (현실적)
  15 bps : taker + 10 bps slippage (보수적)
  20 bps : taker + 15 bps slippage (매우 보수적)

각 비용에서 cooldown_1봉 이 여전히 baseline 이기는가?
거래수가 4배 많은 cooldown 은 비용에 더 민감 — 비용 늘리면 어느 시점 역전?
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
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from autotrader.backtest.crypto_strategies import (
    BacktestConfig, backtest, strat_trend,
)
from autotrader.data.crypto_bars import fetch_crypto_bars

# 같은 종목/세팅 재사용
SYMBOLS = [
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

BARS_PER_YEAR_4H = 2190
BARS_PER_DAY_4H = 6


def overlay_with_block_policy(df, base, trail=0.02, policy="signal_change", cooldown_bars=0):
    out = np.zeros(len(df), dtype=float)
    state = 0; entry = None; hw = lw = None; blocked = 0; stop_bar = -1
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
            blocked = state; stop_bar = i; state = 0; entry = None; hw = lw = None
        if blocked != 0:
            if policy == "none":
                blocked = 0
            elif policy == "cooldown":
                if i - stop_bar >= cooldown_bars: blocked = 0
            elif policy == "signal_change":
                if bp != blocked and bp != 0: blocked = 0
        if state == 0 and bp != 0 and bp != blocked:
            state = bp; entry = p
            hw = p if bp == 1 else None
            lw = p if bp == -1 else None
        out[i] = state
    return pd.Series(out, index=df.index)


def window_metrics(sub):
    rets = sub["net_ret"]
    if len(sub) < 2 or rets.std() == 0: return None
    eq = (1 + rets).cumprod()
    return {
        "return": float(eq.iloc[-1] - 1) * 100,
        "sharpe": float(rets.mean() / rets.std() * np.sqrt(BARS_PER_YEAR_4H)),
        "max_dd": float(((eq / eq.cummax()) - 1).min()) * 100,
        "flips": int((sub["position"].diff().abs() > 0).sum()),
    }


POLICIES = [
    ("A_baseline",    {"policy": "signal_change", "cooldown_bars": 0}),
    ("C_cooldown_1",  {"policy": "cooldown",      "cooldown_bars": 1}),
    ("D_cooldown_2",  {"policy": "cooldown",      "cooldown_bars": 2}),
]
COSTS_BPS = [4.0, 7.0, 10.0, 15.0, 20.0, 30.0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--fast", type=int, default=12)
    ap.add_argument("--slow", type=int, default=48)
    args = ap.parse_args()

    print(f"=== 비용 sensitivity sweep ({args.days}d, {args.window_days}d 윈도우) ===")
    print(f"    정책 {len(POLICIES)}종 × 비용 {len(COSTS_BPS)}레벨 = {len(POLICIES)*len(COSTS_BPS)} 조합")

    window_bars = args.window_days * BARS_PER_DAY_4H

    # 종목별 base_pos + 각 정책별 position series 미리 계산 (비용 무관)
    # 비용은 backtest() 때 cfg 통해 적용
    all_data = {}
    for ccxt_sym, binance_sym in SYMBOLS:
        try:
            df = fetch_crypto_bars(ccxt_sym, timeframe="4h", days=args.days)
        except Exception:
            continue
        if df.empty or len(df) < args.slow + window_bars: continue
        # base position 은 cost 무관 (strat_trend 만)
        cfg_dummy = BacktestConfig(fast_ma=args.fast, slow_ma=args.slow,
                                    cost_bps_per_side=0, bars_per_year=BARS_PER_YEAR_4H)
        base_pos = strat_trend(df, cfg_dummy)
        positions = {}
        for name, opts in POLICIES:
            positions[name] = overlay_with_block_policy(df, base_pos, trail=0.02, **opts)
        all_data[binance_sym] = (df, positions)
        print(f"  loaded {binance_sym}: {len(df)} bars")

    # 비용 레벨별 결과
    print(f"\n{'정책':<14s} {'cost bps':<10s} {'mean Sh':>9s} {'med Sh':>9s} {'mean ret%':>10s} {'mean DD%':>9s} {'Δsh vs A':>10s}")
    print("-" * 80)
    summary_rows = []
    for cost in COSTS_BPS:
        cfg = BacktestConfig(fast_ma=args.fast, slow_ma=args.slow,
                              cost_bps_per_side=cost, bars_per_year=BARS_PER_YEAR_4H)
        per_policy = {}
        for name, _ in POLICIES:
            rows = []
            for sym, (df, positions) in all_data.items():
                pos = positions[name]
                out = backtest(df, pos, cfg)
                n_windows = len(out) // window_bars
                for w in range(n_windows):
                    m = window_metrics(out.iloc[w*window_bars:(w+1)*window_bars])
                    if m: rows.append(m)
            per_policy[name] = rows
        base_sh = np.mean([r["sharpe"] for r in per_policy["A_baseline"]])
        for name, _ in POLICIES:
            r = per_policy[name]
            mean_sh = float(np.mean([x["sharpe"] for x in r]))
            med_sh = float(np.median([x["sharpe"] for x in r]))
            mean_ret = float(np.mean([x["return"] for x in r]))
            mean_dd = float(np.mean([x["max_dd"] for x in r]))
            print(f"{name:<14s} {cost:>6.1f}    {mean_sh:>+9.3f} {med_sh:>+9.3f} "
                  f"{mean_ret:>+9.2f} {mean_dd:>+9.2f} {mean_sh - base_sh:>+10.3f}")
            summary_rows.append(dict(policy=name, cost_bps=cost,
                                     mean_sharpe=mean_sh, median_sharpe=med_sh,
                                     mean_return=mean_ret, mean_dd=mean_dd,
                                     delta_vs_baseline=mean_sh - base_sh))
        print()

    # break-even cost: cooldown_1 의 Δsharpe 가 0 이 되는 비용
    print(f"\n=== break-even 분석 — cooldown_1 이 baseline 보다 못 해지는 비용 ===")
    c1_rows = [r for r in summary_rows if r['policy'] == "C_cooldown_1"]
    for r in c1_rows:
        marker = "    " if r['delta_vs_baseline'] > 0 else " ↓↓ "
        print(f"  비용 {r['cost_bps']:>5.1f} bps: Δsh = {r['delta_vs_baseline']:+.3f}{marker}"
              f"sh={r['mean_sharpe']:+.3f}, ret={r['mean_return']:+.2f}%")

    # save
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = ROOT / "data" / f"backtest_block_costs_{args.days}d_{ts}.parquet"
    pd.DataFrame(summary_rows).to_parquet(out_path)
    print(f"\n[saved] {out_path}")


if __name__ == "__main__":
    main()
