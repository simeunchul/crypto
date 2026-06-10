"""Crypto SWING sweep — 4h / 1d 봉으로 진짜 swing-trade 가능한지 검증.

질문: "옵션 A (1h + 6/24 + Trail2) 는 검증된 Sharpe +2.76. 더 긴 봉(4h, 1d) +
       정통 swing MA 조합 (20/60, 50/200) 이 이를 이길 수 있는가?"

Test matrix:
  - Intervals:  4h, 1d
  - MA combos:  (5, 20), (10, 30), (20, 60), (50, 200)
  - Exit rules: flip (no overlay), trail2, trail3, trail5, atr2, atr3
  - Strategy:   B_trend (long-short)
  - Symbols:    BTC, ETH, SOL, AVAX, BNB
  - Period:     365일

Baseline: 1h + 6/24 + Trail2 (옵션 A, 운영봇 swing 모드 = 검증된 +2.76 Sharpe).

각 조합의 Sharpe / Return / MaxDD / Trade 횟수 비교 → swing preset 후보 탐색.
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
from autotrader.backtest.crypto_strategies import (
    BacktestConfig, backtest, summarize,
    apply_risk_overlay, apply_atr_trailing,
    strat_trend,
)


SYMBOL_MAP = {
    # 검증된 5종 (기존)
    "BTC/USDT:USDT":  "BTCUSDT",
    "ETH/USDT:USDT":  "ETHUSDT",
    "SOL/USDT:USDT":  "SOLUSDT",
    "AVAX/USDT:USDT": "AVAXUSDT",
    "BNB/USDT:USDT":  "BNBUSDT",
    # 알트 메이저 확장 — 시총 / 거래량 상위 + Binance USDT-M perp 상장
    "DOGE/USDT:USDT": "DOGEUSDT",
    "ADA/USDT:USDT":  "ADAUSDT",
    "XRP/USDT:USDT":  "XRPUSDT",
    "MATIC/USDT:USDT": "MATICUSDT",
    "DOT/USDT:USDT":  "DOTUSDT",
    "LINK/USDT:USDT": "LINKUSDT",
    "LTC/USDT:USDT":  "LTCUSDT",
    "BCH/USDT:USDT":  "BCHUSDT",
    "TRX/USDT:USDT":  "TRXUSDT",
    "ARB/USDT:USDT":  "ARBUSDT",
    "OP/USDT:USDT":   "OPUSDT",
    "SUI/USDT:USDT":  "SUIUSDT",
    "INJ/USDT:USDT":  "INJUSDT",
    "NEAR/USDT:USDT": "NEARUSDT",
    "ATOM/USDT:USDT": "ATOMUSDT",
}


# (interval, bars_per_year)
INTERVAL_BPY = {
    "1h": 8760,
    "4h": 2190,    # 6 × 365
    "1d": 365,
}


# (fast_bars, slow_bars, label) — bar 단위. interval 따라 실제 시간 다름.
MA_COMBOS_BY_INTERVAL = {
    "4h": [
        (3, 12,   "12h/48h"),     # 짧음
        (5, 20,   "20h/80h"),     # ≈ 1d/3d
        (10, 30,  "40h/120h"),    # ≈ 1.7d/5d
        (20, 60,  "80h/240h"),    # ≈ 3.3d/10d (정통 swing)
        (50, 200, "8d/33d"),      # 매우 김
    ],
    "1d": [
        (5, 20,   "5d/20d"),       # 정통 swing
        (10, 30,  "10d/30d"),      # ≈ 1m/1q
        (20, 60,  "20d/60d"),      # ≈ 1m/2m
        (50, 200, "50d/200d"),     # 정통 positional (golden cross)
    ],
}


# Exit rules: (label, factory_fn) where factory_fn(df, base_pos) → pos
def _make_exit_funcs():
    return {
        "flip":   lambda df, base, cfg: base,                                              # no overlay
        "trail2": lambda df, base, cfg: apply_risk_overlay(df, base, trailing_stop_pct=0.02),
        "trail3": lambda df, base, cfg: apply_risk_overlay(df, base, trailing_stop_pct=0.03),
        "trail5": lambda df, base, cfg: apply_risk_overlay(df, base, trailing_stop_pct=0.05),
        "atr2":   lambda df, base, cfg: apply_atr_trailing(df, base, atr_window=14, atr_multiple=2.0),
        "atr3":   lambda df, base, cfg: apply_atr_trailing(df, base, atr_window=14, atr_multiple=3.0),
    }


def run_baseline_optionA(days: int, rows: list):
    """Baseline: 옵션 A — 1h + 6/24 + Trail2. 비교 기준선."""
    print(f"\n--- BASELINE (옵션 A): 1h + 6/24 + Trail2 ---")
    cfg = BacktestConfig(fast_ma=6, slow_ma=24, bars_per_year=8760)
    exit_funcs = _make_exit_funcs()
    for ccxt_sym, binance_sym in SYMBOL_MAP.items():
        try:
            price = fetch_crypto_bars(ccxt_sym, timeframe="1h", days=days)
        except Exception as e:
            print(f"  {binance_sym}: fetch fail {e}"); continue
        if price.empty or len(price) < 300:
            print(f"  {binance_sym}: insufficient ({len(price)} bars)"); continue
        base_pos = strat_trend(price, cfg)
        pos = exit_funcs["trail2"](price, base_pos, cfg)
        out = backtest(price, pos, cfg)
        s = summarize(out, cfg)
        rows.append({
            "scenario": "baseline_optionA",
            "symbol": binance_sym, "interval": "1h",
            "fast_ma": 6, "slow_ma": 24, "ma_label": "6h/24h*",
            "exit_rule": "trail2",
            **s,
        })
        print(f"  {binance_sym:>8}: ret={s['total_return_pct']:+7.2f}% Sharpe={s['sharpe']:+5.2f} DD={s['max_drawdown_pct']:+6.2f}% trades={int(s['n_position_flips'])}")


def run_swing_matrix(days: int, rows: list):
    """매트릭스 4h/1d × MA combos × exit rules."""
    exit_funcs = _make_exit_funcs()

    for interval, ma_combos in MA_COMBOS_BY_INTERVAL.items():
        bpy = INTERVAL_BPY[interval]
        print(f"\n--- Interval: {interval} ---")
        for ccxt_sym, binance_sym in SYMBOL_MAP.items():
            try:
                price = fetch_crypto_bars(ccxt_sym, timeframe=interval, days=days)
            except Exception as e:
                print(f"  {binance_sym}: fetch fail {e}"); continue
            if price.empty:
                print(f"  {binance_sym}: empty data"); continue
            min_bars_needed = max(slow for _, slow, _ in ma_combos) + 20
            if len(price) < min_bars_needed:
                print(f"  {binance_sym}: insufficient ({len(price)} < {min_bars_needed} bars)"); continue
            print(f"  {binance_sym}: {len(price)} bars")

            for fast, slow, label in ma_combos:
                if len(price) < slow + 5:
                    continue
                cfg = BacktestConfig(fast_ma=fast, slow_ma=slow,
                                      bb_window=20, bars_per_year=bpy)
                base_pos = strat_trend(price, cfg)

                for exit_label, fn in exit_funcs.items():
                    try:
                        pos = fn(price, base_pos, cfg)
                        out = backtest(price, pos, cfg)
                        s = summarize(out, cfg)
                        rows.append({
                            "scenario": "swing_matrix",
                            "symbol": binance_sym,
                            "interval": interval,
                            "fast_ma": fast, "slow_ma": slow, "ma_label": label,
                            "exit_rule": exit_label,
                            **s,
                        })
                    except Exception as e:
                        print(f"    {binance_sym} {label} {exit_label} fail: {e}")


def print_topN(df: pd.DataFrame, n: int = 15):
    """Sharpe 기준 top N 조합."""
    top = df.sort_values("sharpe", ascending=False).head(n)
    print(f"\n{'='*120}")
    print(f"TOP {n} (Sharpe 기준):")
    print(f"{'='*120}")
    print(f"{'scenario':>17} {'symbol':>8} {'int':>3} {'ma_label':>12} {'exit':>6} {'ret%':>8} {'Sharpe':>7} {'maxDD%':>8} {'trades':>7}")
    print("-" * 120)
    for _, r in top.iterrows():
        print(f"{r['scenario']:>17} {r['symbol']:>8} {r['interval']:>3} {r['ma_label']:>12} {r['exit_rule']:>6} "
              f"{r['total_return_pct']:>+8.2f} {r['sharpe']:>+7.2f} {r['max_drawdown_pct']:>+8.2f} {int(r['n_position_flips']):>7}")


def print_avg_per_combo(df: pd.DataFrame):
    """5종 평균 — interval × MA × exit rule 단위."""
    print(f"\n{'='*120}")
    print(f"평균 (5종) — interval × MA × exit_rule:")
    print(f"{'='*120}")
    print(f"{'int':>3} {'ma_label':>12} {'exit':>6} {'avg_ret%':>9} {'avg_Sharpe':>11} {'avg_DD%':>9} {'avg_trades':>11}")
    print("-" * 120)
    g = df.groupby(["interval", "ma_label", "exit_rule"]).agg(
        avg_ret=("total_return_pct", "mean"),
        avg_sharpe=("sharpe", "mean"),
        avg_dd=("max_drawdown_pct", "mean"),
        avg_trades=("n_position_flips", "mean"),
    ).reset_index().sort_values("avg_sharpe", ascending=False)
    for _, r in g.iterrows():
        print(f"{r['interval']:>3} {r['ma_label']:>12} {r['exit_rule']:>6} "
              f"{r['avg_ret']:>+9.2f} {r['avg_sharpe']:>+11.2f} {r['avg_dd']:>+9.2f} {r['avg_trades']:>11.0f}")
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365, help="백테스트 기간 (default 365)")
    ap.add_argument("--skip-baseline", action="store_true",
                    help="옵션 A baseline (1h+6/24+Trail2) 스킵")
    args = ap.parse_args()

    print(f"=== Crypto SWING Sweep — {args.days}d ===")
    print(f"  Intervals: {list(INTERVAL_BPY.keys() - {'1h'})}")
    print(f"  Symbols  : {list(SYMBOL_MAP.values())}")
    print(f"  Exits    : flip, trail2/3/5, atr2/3")

    rows: list[dict] = []

    if not args.skip_baseline:
        run_baseline_optionA(args.days, rows)

    run_swing_matrix(args.days, rows)

    if not rows:
        print("\nNo results."); return

    df = pd.DataFrame(rows)
    df = df.dropna(subset=["sharpe"])

    # save
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_parquet = ROOT / "data" / f"backtest_crypto_swing_{args.days}d_{ts}.parquet"
    df.to_parquet(out_parquet)
    print(f"\n[saved] {out_parquet}")

    # top N + averages
    print_topN(df, n=15)
    avg_df = print_avg_per_combo(df[df["scenario"] == "swing_matrix"])

    # 최종 의사결정 표
    print(f"\n{'='*120}")
    print(f"DECISION — 최고 평균 Sharpe 조합 vs 옵션 A baseline:")
    print(f"{'='*120}")
    if not avg_df.empty:
        best = avg_df.iloc[0]
        print(f"  swing 매트릭스 최고: {best['interval']} + {best['ma_label']} + {best['exit_rule']}")
        print(f"    avg_Sharpe={best['avg_sharpe']:+.2f}  avg_ret={best['avg_ret']:+.2f}%  avg_DD={best['avg_dd']:+.2f}%  avg_trades={best['avg_trades']:.0f}")
    baseline = df[df["scenario"] == "baseline_optionA"]
    if not baseline.empty:
        b_sharpe = baseline["sharpe"].mean()
        b_ret = baseline["total_return_pct"].mean()
        b_dd = baseline["max_drawdown_pct"].mean()
        b_tr = baseline["n_position_flips"].mean()
        print(f"  옵션 A baseline (1h+6/24+Trail2):")
        print(f"    avg_Sharpe={b_sharpe:+.2f}  avg_ret={b_ret:+.2f}%  avg_DD={b_dd:+.2f}%  avg_trades={b_tr:.0f}")

    # JSON summary
    summary = {
        "params": {
            "days": args.days,
            "intervals": list(MA_COMBOS_BY_INTERVAL.keys()),
            "symbols": list(SYMBOL_MAP.values()),
            "exit_rules": list(_make_exit_funcs().keys()),
        },
        "all_rows": rows,
        "avg_per_combo": avg_df.to_dict("records") if not avg_df.empty else [],
    }
    summary_path = ROOT / "data" / f"backtest_crypto_swing_{args.days}d_{ts}_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n[saved] summary: {summary_path}")


if __name__ == "__main__":
    main()
