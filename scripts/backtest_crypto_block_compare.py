"""차단 정책 비교 — trail-stop 후 같은 방향 재진입 룰.

질문: "SHORT 익절 후 가격이 잠깐 반등했다가 다시 하락하는 케이스가 있는데
       현재는 신호 방향 바뀔 때까지 못 들어감 → 큰 추세 후반 놓침.
       시간 기반 cooldown 또는 차단 해제가 더 나은가?"

후보 정책:
  A. baseline       : 신호 방향 바뀔 때까지 차단 (현재 운영봇, opt-Y/Z 검증)
  B. no_block       : 즉시 같은 방향 재진입 허용 (휩쏘 위험 vs 추세 후반 포착)
  C. cooldown_1bar  : 1 봉(4h) 차단 후 해제
  D. cooldown_2bar  : 2 봉(8h)
  E. cooldown_4bar  : 4 봉(16h)
  F. cooldown_8bar  : 8 봉(32h ≈ 1.3일)

세팅: 4h × MA 12/48 × trail 2% × long-short × 18종 × 365일.
30일 비중첩 윈도우 11개 walk-forward.
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


def overlay_with_block_policy(df: pd.DataFrame, base: pd.Series,
                                trail: float = 0.02,
                                policy: str = "signal_change",
                                cooldown_bars: int = 0) -> pd.Series:
    """trail-stop 후 같은 방향 재진입 차단 정책 비교 가능 버전.

    policy:
      "signal_change" : 신호 방향 바뀔 때까지 차단 (baseline)
      "none"          : 차단 안 함, 즉시 재진입 가능
      "cooldown"      : trail-stop 후 cooldown_bars 봉 지나면 해제
    """
    out = np.zeros(len(df), dtype=float)
    state = 0
    entry = None
    hw = lw = None
    blocked = 0
    stop_bar = -1   # cooldown 측정용 — trail-stop 발동된 봉 index
    closes = df["close"].values
    bps = base.fillna(0).astype(int).values

    for i in range(len(df)):
        p = float(closes[i])
        bp = int(bps[i])

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
            stop_bar = i
            state = 0; entry = None; hw = lw = None

        # 차단 해제 로직
        if blocked != 0:
            if policy == "none":
                blocked = 0
            elif policy == "cooldown":
                if i - stop_bar >= cooldown_bars:
                    blocked = 0
            elif policy == "signal_change":
                if bp != blocked and bp != 0:
                    blocked = 0
                # bp == 0 또는 bp == blocked 면 계속 차단

        # 진입
        if state == 0 and bp != 0 and bp != blocked:
            state = bp
            entry = p
            hw = p if bp == 1 else None
            lw = p if bp == -1 else None

        out[i] = state

    return pd.Series(out, index=df.index)


def classify_regime(bnh_pct: float) -> str:
    a = abs(bnh_pct)
    if a >= 15: return "STRONG_TREND"
    if a >= 6:  return "WEAK_TREND"
    return "RANGE"


def window_metrics(sub: pd.DataFrame) -> dict | None:
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
    ("A_baseline_signal",   {"policy": "signal_change", "cooldown_bars": 0}),
    ("B_no_block",          {"policy": "none",          "cooldown_bars": 0}),
    ("C_cooldown_1bar_4h",  {"policy": "cooldown",      "cooldown_bars": 1}),
    ("D_cooldown_2bar_8h",  {"policy": "cooldown",      "cooldown_bars": 2}),
    ("E_cooldown_4bar_16h", {"policy": "cooldown",      "cooldown_bars": 4}),
    ("F_cooldown_8bar_32h", {"policy": "cooldown",      "cooldown_bars": 8}),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--fast", type=int, default=12)
    ap.add_argument("--slow", type=int, default=48)
    args = ap.parse_args()

    print(f"=== Block-policy compare — {args.days}d / {args.window_days}d 윈도우 ===")
    print(f"    정책 {len(POLICIES)}종, 18종, 4h+{args.fast}/{args.slow}+trail2")

    cfg = BacktestConfig(fast_ma=args.fast, slow_ma=args.slow,
                          cost_bps_per_side=4.0, bars_per_year=BARS_PER_YEAR_4H)
    window_bars = args.window_days * BARS_PER_DAY_4H

    rows: list[dict] = []
    for ccxt_sym, binance_sym in SYMBOLS:
        try:
            df = fetch_crypto_bars(ccxt_sym, timeframe="4h", days=args.days)
        except Exception as e:
            print(f"  {binance_sym}: fetch fail {e}"); continue
        if df.empty or len(df) < args.slow + window_bars: continue
        base_pos = strat_trend(df, cfg)

        # 각 정책으로 position 계산 + 윈도우 metric 추출
        outs = {}
        for name, opts in POLICIES:
            pos = overlay_with_block_policy(df, base_pos, trail=0.02, **opts)
            outs[name] = backtest(df, pos, cfg)

        n_windows = len(next(iter(outs.values()))) // window_bars
        for w in range(n_windows):
            lo = w * window_bars; hi = lo + window_bars
            row = {"symbol": binance_sym, "window": w}
            sub_close = outs[POLICIES[0][0]].iloc[lo:hi]["close"]
            if len(sub_close) < 2: continue
            bnh = float((sub_close.iloc[-1] / sub_close.iloc[0] - 1) * 100)
            row["bnh_pct"] = bnh
            row["regime"] = classify_regime(bnh)
            valid = True
            for name, _ in POLICIES:
                m = window_metrics(outs[name].iloc[lo:hi])
                if m is None:
                    valid = False; break
                row[f"{name}_sharpe"] = m["sharpe"]
                row[f"{name}_return"] = m["return"]
                row[f"{name}_dd"] = m["max_dd"]
                row[f"{name}_flips"] = m["flips"]
            if valid: rows.append(row)
        print(f"  {binance_sym}: {n_windows} windows")

    if not rows:
        print("NO DATA"); return

    df_res = pd.DataFrame(rows)
    n = len(df_res)
    print(f"\n=== 전체 평균 (n={n} 윈도우) ===")
    print(f"  {'정책':<24s} {'mean_sh':>8s} {'med_sh':>8s} {'mean_ret%':>10s} "
          f"{'mean_dd%':>9s} {'mean_flips':>10s} {'Δsh vs A':>10s}")
    base_sh = df_res["A_baseline_signal_sharpe"].mean()
    for name, _ in POLICIES:
        sh_mean = df_res[f"{name}_sharpe"].mean()
        sh_med = df_res[f"{name}_sharpe"].median()
        ret = df_res[f"{name}_return"].mean()
        dd = df_res[f"{name}_dd"].mean()
        fl = df_res[f"{name}_flips"].mean()
        delta = sh_mean - base_sh
        print(f"  {name:<24s} {sh_mean:>+8.3f} {sh_med:>+8.3f} {ret:>+9.2f} {dd:>+9.2f} "
              f"{fl:>10.1f} {delta:>+10.3f}")

    # head-to-head 승률 (각 정책이 baseline 대비 이긴 윈도우 %)
    print(f"\n=== baseline (A) 대비 sharpe 승률 ===")
    print(f"  {'정책':<24s} {'win%':>6s} {'좋은 윈도우':>10s} {'동률':>6s} {'더 나쁜':>8s}")
    for name, _ in POLICIES:
        if name == "A_baseline_signal": continue
        better = (df_res[f"{name}_sharpe"] > df_res["A_baseline_signal_sharpe"]).sum()
        worse = (df_res[f"{name}_sharpe"] < df_res["A_baseline_signal_sharpe"]).sum()
        tie = n - better - worse
        win_pct = better / n * 100
        print(f"  {name:<24s} {win_pct:>5.1f}% {better:>10d} {tie:>6d} {worse:>8d}")

    print(f"\n=== regime 별 mean sharpe ===")
    print(f"  {'regime':<14s} {'n':>4s} "
          + " ".join(f"{name.split('_',1)[1][:10]:>10s}" for name, _ in POLICIES))
    for reg in ["STRONG_TREND", "WEAK_TREND", "RANGE"]:
        g = df_res[df_res["regime"] == reg]
        if g.empty: continue
        vals = " ".join(f"{g[f'{name}_sharpe'].mean():>+10.3f}" for name, _ in POLICIES)
        print(f"  {reg:<14s} {len(g):>4d} {vals}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = ROOT / "data" / f"backtest_block_compare_{args.days}d_{ts}.parquet"
    df_res.to_parquet(out_path)
    print(f"\n[saved] {out_path}")


if __name__ == "__main__":
    main()
