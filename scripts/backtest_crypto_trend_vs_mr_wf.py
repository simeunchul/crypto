"""Trend vs Mean-Reversion walk-forward — 변동성 국면별 비교.

질문 (사용자): "횡보장에서도 먹으려면 mean-reversion 이 정말 필요한가?
              저변동성 박스권에서 MR 이 trend-follow 보다 나은가?"

방법:
  - 365일 4h 데이터 → 30일 비중첩 윈도우
  - 각 윈도우의 실현변동성(realized volatility) 계산 → 분위수로 LOW/MID/HIGH 분류
  - 각 윈도우에서 두 전략 성과 측정:
      TREND: long-short 4h+12/48 + trail-2%        (현 운영 봇)
      MR   : Bollinger 20/2σ mean reversion         (횡보용 후보)
  - 변동성 국면별 TREND vs MR 평균 성과 비교

해석:
  LOW_VOL 에서 MR > TREND  → 횡보장엔 MR 추가 가치 있음 (regime-switching 정당)
  LOW_VOL 에서도 TREND ≥ MR → MR 불필요, 그냥 trend 유지

전체 365d position 을 한 번 계산 후 (warmup 자동) 윈도우별 분해 — walk-forward 정신 유지.
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
    BacktestConfig, backtest, apply_risk_overlay, strat_trend, strat_bollinger_mr,
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
BARS_PER_DAY_4H = 6


def window_perf(sub: pd.DataFrame) -> dict:
    """한 윈도우 구간의 net_ret 로 성과 측정."""
    rets = sub["net_ret"]
    if len(sub) < 2 or rets.std() == 0:
        return {"ret_pct": 0.0, "sharpe": float("nan"), "dd_pct": 0.0, "flips": 0}
    eq = (1 + rets).cumprod()
    return {
        "ret_pct": float((eq.iloc[-1] - 1) * 100),
        "sharpe": float(rets.mean() / rets.std() * np.sqrt(BARS_PER_YEAR_4H)),
        "dd_pct": float(((eq / eq.cummax()) - 1).min() * 100),
        "flips": int((sub["position"].diff().abs() > 0).sum()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--trail-pct", type=float, default=0.02)
    ap.add_argument("--fast", type=int, default=12)
    ap.add_argument("--slow", type=int, default=48)
    ap.add_argument("--bb-window", type=int, default=20)
    ap.add_argument("--bb-std", type=float, default=2.0)
    args = ap.parse_args()

    print(f"=== Trend vs MR walk-forward — {args.days}d, {args.window_days}d 윈도우 ===")
    print(f"  TREND: long-short 4h+{args.fast}/{args.slow}+trail{args.trail_pct:.0%}")
    print(f"  MR   : Bollinger {args.bb_window}/{args.bb_std}σ mean-reversion")

    cfg = BacktestConfig(fast_ma=args.fast, slow_ma=args.slow,
                         bb_window=args.bb_window, bb_std=args.bb_std,
                         cost_bps_per_side=4.0, bars_per_year=BARS_PER_YEAR_4H)
    window_bars = args.window_days * BARS_PER_DAY_4H

    rows = []
    for ccxt_sym, binance_sym in SYMBOLS:
        try:
            df = fetch_crypto_bars(ccxt_sym, timeframe="4h", days=args.days)
        except Exception as e:
            print(f"  {binance_sym}: fetch fail {e}"); continue
        if df.empty or len(df) < args.slow + window_bars:
            print(f"  {binance_sym}: insufficient ({len(df)} bars)"); continue

        # TREND position + backtest
        trend_pos = apply_risk_overlay(df, strat_trend(df, cfg), trailing_stop_pct=args.trail_pct)
        out_trend = backtest(df, trend_pos, cfg)
        # MR position + backtest
        mr_pos = strat_bollinger_mr(df, cfg)
        out_mr = backtest(df, mr_pos, cfg)

        # 30일 비중첩 윈도우 분해
        n = len(df)
        n_windows = n // window_bars
        ret_series = df["close"].pct_change()
        for w in range(n_windows):
            lo, hi = w * window_bars, (w + 1) * window_bars
            sub_t = out_trend.iloc[lo:hi]
            sub_m = out_mr.iloc[lo:hi]
            if len(sub_t) < 2:
                continue
            # 실현변동성 (구간 4h 수익률 std, 연환산)
            rv = float(ret_series.iloc[lo:hi].std() * np.sqrt(BARS_PER_YEAR_4H))
            pt = window_perf(sub_t)
            pm = window_perf(sub_m)
            rows.append({
                "symbol": binance_sym, "window": w,
                "window_start": str(df.index[lo].date()),
                "realized_vol": rv,
                "trend_ret": pt["ret_pct"], "trend_sharpe": pt["sharpe"], "trend_dd": pt["dd_pct"], "trend_flips": pt["flips"],
                "mr_ret": pm["ret_pct"], "mr_sharpe": pm["sharpe"], "mr_dd": pm["dd_pct"], "mr_flips": pm["flips"],
            })
        print(f"  {binance_sym}: {n_windows} windows")

    if not rows:
        print("No results."); return

    df_res = pd.DataFrame(rows)

    # 변동성 분위수로 LOW/MID/HIGH 분류 (전체 윈도우 기준 33/67 percentile)
    q33 = df_res["realized_vol"].quantile(0.33)
    q67 = df_res["realized_vol"].quantile(0.67)
    def vol_regime(rv):
        if rv <= q33:
            return "LOW_VOL"
        elif rv <= q67:
            return "MID_VOL"
        return "HIGH_VOL"
    df_res["vol_regime"] = df_res["realized_vol"].apply(vol_regime)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = ROOT / "data" / f"backtest_crypto_trend_vs_mr_{args.days}d_{ts}.parquet"
    df_res.to_parquet(out_path)
    print(f"\n[saved] {out_path}")
    print(f"  변동성 분위수: LOW ≤ {q33:.2f} < MID ≤ {q67:.2f} < HIGH (연환산 vol)")

    # ── 변동성 국면별 TREND vs MR 비교 (핵심)
    print(f"\n{'='*100}")
    print(f"변동성 국면별 — TREND vs MR (사용자 질문의 답)")
    print(f"{'='*100}")
    print(f"{'vol_regime':>10} {'n':>4} | {'TREND_ret%':>11} {'TREND_Shrp':>11} {'TREND_승률':>10} | {'MR_ret%':>9} {'MR_Shrp':>9} {'MR_승률':>9} | {'승자':>6}")
    print("-" * 100)
    regime_summary = []
    for reg in ["LOW_VOL", "MID_VOL", "HIGH_VOL"]:
        g = df_res[df_res["vol_regime"] == reg]
        if g.empty:
            continue
        t_ret = g["trend_ret"].mean(); t_shrp = g["trend_sharpe"].mean(); t_win = (g["trend_ret"] > 0).mean() * 100
        m_ret = g["mr_ret"].mean();    m_shrp = g["mr_sharpe"].mean();    m_win = (g["mr_ret"] > 0).mean() * 100
        winner = "TREND" if t_ret > m_ret else "MR"
        print(f"{reg:>10} {len(g):>4} | {t_ret:>+11.2f} {t_shrp:>+11.2f} {t_win:>9.1f}% | {m_ret:>+9.2f} {m_shrp:>+9.2f} {m_win:>8.1f}% | {winner:>6}")
        regime_summary.append({
            "vol_regime": reg, "n": len(g),
            "trend_ret": t_ret, "trend_sharpe": t_shrp, "trend_win": t_win,
            "mr_ret": m_ret, "mr_sharpe": m_shrp, "mr_win": m_win,
            "winner": winner,
        })

    # ── 전체 비교
    print(f"\n{'='*70}")
    print(f"전체 평균 (모든 윈도우 {len(df_res)} 표본)")
    print(f"{'='*70}")
    print(f"  TREND : ret={df_res['trend_ret'].mean():+.2f}%  Sharpe={df_res['trend_sharpe'].mean():+.2f}  승률={ (df_res['trend_ret']>0).mean()*100:.1f}%")
    print(f"  MR    : ret={df_res['mr_ret'].mean():+.2f}%  Sharpe={df_res['mr_sharpe'].mean():+.2f}  승률={ (df_res['mr_ret']>0).mean()*100:.1f}%")

    # ── 결론
    print(f"\n{'='*70}")
    print(f"결론")
    print(f"{'='*70}")
    low = next((r for r in regime_summary if r["vol_regime"] == "LOW_VOL"), None)
    if low:
        if low["mr_ret"] > low["trend_ret"]:
            print(f"  ✅ 저변동성(LOW_VOL)에서 MR({low['mr_ret']:+.2f}%) > TREND({low['trend_ret']:+.2f}%)")
            print(f"     → 횡보장 MR 추가 가치 있음. regime-switching 정당.")
        else:
            print(f"  ❌ 저변동성(LOW_VOL)에서도 TREND({low['trend_ret']:+.2f}%) ≥ MR({low['mr_ret']:+.2f}%)")
            print(f"     → MR 추가 불필요. 그냥 trend 유지가 나음.")

    summary = {
        "params": {"days": args.days, "window_days": args.window_days,
                   "trail_pct": args.trail_pct, "fast": args.fast, "slow": args.slow,
                   "bb_window": args.bb_window, "bb_std": args.bb_std,
                   "vol_q33": float(q33), "vol_q67": float(q67),
                   "symbols": [s[1] for s in SYMBOLS]},
        "regime_summary": regime_summary,
        "overall": {
            "trend_ret": float(df_res["trend_ret"].mean()),
            "trend_sharpe": float(df_res["trend_sharpe"].mean()),
            "mr_ret": float(df_res["mr_ret"].mean()),
            "mr_sharpe": float(df_res["mr_sharpe"].mean()),
        },
    }
    summary_path = ROOT / "data" / f"backtest_crypto_trend_vs_mr_{args.days}d_{ts}_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n[saved] {summary_path}")


if __name__ == "__main__":
    main()
