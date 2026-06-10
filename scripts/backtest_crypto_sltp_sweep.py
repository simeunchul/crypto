"""Crypto SL/TP/Trailing variant sweep on 365d daily data.

Base strategies (active only, buyhold 제외):
  B_trend, C_trend_long_only, D_trend+funding, G_bollinger_mr, H_funding_contra

Variants:
  - none (base)                 : 기존
  - SL -3%
  - SL -5%
  - SL -3% + TP +5%
  - SL -3% + TP +10%
  - Trailing -2%
  - Trailing -3%
  - Trailing -5%

각 (symbol × strategy × variant) 조합 → return / Sharpe / MaxDD / 매매 횟수.

목표: 어떤 (strategy, SL/TP) 결합이 최고 risk-adjusted 인지.
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
from autotrader.data.crypto_signals import fetch_funding_rate
from autotrader.backtest.crypto_strategies import (
    BacktestConfig, STRATEGIES, backtest, summarize, merge_signals,
    apply_risk_overlay, make_strategy_with_overlay,
    apply_atr_trailing, apply_trend_adaptive_trailing,
    apply_trailing_only,
)


SYMBOL_MAP = {
    "BTC/USDT:USDT": "BTCUSDT",
    "ETH/USDT:USDT": "ETHUSDT",
    "SOL/USDT:USDT": "SOLUSDT",
    "AVAX/USDT:USDT": "AVAXUSDT",
    "BNB/USDT:USDT": "BNBUSDT",
}

# Active strategies only (buyhold 의미 없으니 제외)
ACTIVE_STRATEGIES = ["B_trend", "C_trend_long_only", "D_trend+funding",
                      "G_bollinger_mr", "H_funding_contra"]

# SL/TP/Trail variants — fixed
FIXED_VARIANTS = [
    ("none",         {}),
    ("SL3",          {"stop_loss_pct": 0.03}),
    ("SL5",          {"stop_loss_pct": 0.05}),
    ("SL3_TP5",      {"stop_loss_pct": 0.03, "take_profit_pct": 0.05}),
    ("SL3_TP10",     {"stop_loss_pct": 0.03, "take_profit_pct": 0.10}),
    ("Trail2",       {"trailing_stop_pct": 0.02}),
    ("Trail3",       {"trailing_stop_pct": 0.03}),
    ("Trail5",       {"trailing_stop_pct": 0.05}),
]

# ATR-based variants
ATR_VARIANTS = [
    ("ATR2",  {"atr_window": 14, "atr_multiple": 2.0}),
    ("ATR3",  {"atr_window": 14, "atr_multiple": 3.0}),
    ("ATR5",  {"atr_window": 14, "atr_multiple": 5.0}),
]

# Trend-strength adaptive (사용자 제안)
ADAPTIVE_VARIANTS = [
    ("AdaptT_2_3_5",  {"weak_trail": 0.02, "medium_trail": 0.03, "strong_trail": 0.05}),
    ("AdaptT_2_4_8",  {"weak_trail": 0.02, "medium_trail": 0.04, "strong_trail": 0.08}),
]

# Trailing-only exit (사용자 제안: signal 반전 무시, trailing 만으로 청산)
TRAIL_ONLY_VARIANTS = [
    ("TrailOnly2",  {"trailing_stop_pct": 0.02}),
    ("TrailOnly3",  {"trailing_stop_pct": 0.03}),
    ("TrailOnly5",  {"trailing_stop_pct": 0.05}),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--interval", default="1d")
    args = ap.parse_args()

    if args.interval == "1d":
        bars_per_year = 365
        cfg = BacktestConfig(fast_ma=6, slow_ma=24, bb_window=20,
                              bb_std=2.0, bars_per_year=bars_per_year)
    else:
        bars_per_year = 8760
        cfg = BacktestConfig(bars_per_year=bars_per_year)

    total_variants = (len(FIXED_VARIANTS) + len(ATR_VARIANTS)
                       + len(ADAPTIVE_VARIANTS) + len(TRAIL_ONLY_VARIANTS))
    print(f"=== SL/TP/Trail sweep — {args.days}d × {args.interval} ===")
    print(f"  active strategies: {len(ACTIVE_STRATEGIES)}")
    print(f"  variants: {total_variants} (fixed {len(FIXED_VARIANTS)} + ATR {len(ATR_VARIANTS)} + adaptive {len(ADAPTIVE_VARIANTS)} + TrailOnly {len(TRAIL_ONLY_VARIANTS)})")
    print(f"  symbols:           {len(SYMBOL_MAP)}")
    print(f"  total runs:        {len(ACTIVE_STRATEGIES) * total_variants * len(SYMBOL_MAP)}")

    rows = []
    for ccxt_sym in SYMBOL_MAP.keys():
        binance_sym = SYMBOL_MAP[ccxt_sym]
        print(f"\n=== {ccxt_sym} ===")

        price = fetch_crypto_bars(ccxt_sym, timeframe=args.interval, days=args.days)
        if price.empty:
            print(f"  empty"); continue

        funding = fetch_funding_rate(binance_sym, days=args.days)
        df = merge_signals(price, funding)
        if "funding_rate" in df.columns:
            df = df.dropna(subset=["funding_rate"])
        if df.empty:
            print(f"  merged empty"); continue
        print(f"  data: {len(df):>4} bars")

        for strat_name in ACTIVE_STRATEGIES:
            base_fn = STRATEGIES[strat_name]
            base_pos = base_fn(df, cfg)

            # Fixed variants (SL/TP/Trail)
            for variant_name, kwargs in FIXED_VARIANTS:
                pos = base_pos if not kwargs else apply_risk_overlay(df, base_pos, **kwargs)
                out = backtest(df, pos, cfg)
                s = summarize(out, cfg)
                rows.append({"symbol": binance_sym, "strategy": strat_name,
                              "variant": variant_name, **s})

            # ATR-based variants — needs high/low columns
            if "high" in df.columns and "low" in df.columns:
                for variant_name, kwargs in ATR_VARIANTS:
                    pos = apply_atr_trailing(df, base_pos, **kwargs)
                    out = backtest(df, pos, cfg)
                    s = summarize(out, cfg)
                    rows.append({"symbol": binance_sym, "strategy": strat_name,
                                  "variant": variant_name, **s})

            # Trend-adaptive variants
            for variant_name, kwargs in ADAPTIVE_VARIANTS:
                pos = apply_trend_adaptive_trailing(df, base_pos,
                                                       fast_ma=cfg.fast_ma,
                                                       slow_ma=cfg.slow_ma,
                                                       **kwargs)
                out = backtest(df, pos, cfg)
                s = summarize(out, cfg)
                rows.append({"symbol": binance_sym, "strategy": strat_name,
                              "variant": variant_name, **s})

            # Trailing-only (signal 반전 무시)
            for variant_name, kwargs in TRAIL_ONLY_VARIANTS:
                pos = apply_trailing_only(df, base_pos, **kwargs)
                out = backtest(df, pos, cfg)
                s = summarize(out, cfg)
                rows.append({"symbol": binance_sym, "strategy": strat_name,
                              "variant": variant_name, **s})

    if not rows:
        print("\nNo results.")
        return

    df_results = pd.DataFrame(rows)
    # Drop NaN Sharpe rows (no-trade configs)
    df_results = df_results.dropna(subset=["sharpe"])
    df_results = df_results.sort_values(["symbol", "sharpe"], ascending=[True, False])

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = ROOT / "data" / f"backtest_crypto_sltp_{args.days}d_{ts}.parquet"
    df_results.to_parquet(out_path)
    print(f"\n[saved] {out_path}")

    # Best variant per (symbol, strategy)
    print()
    print("=" * 100)
    print("Best variant per (symbol, strategy):")
    print("=" * 100)
    print(f"{'symbol':>8} {'strategy':>22} {'variant':>10} {'return%':>8} {'Sharpe':>7} {'maxDD%':>8} {'flips':>6}")
    print("-" * 100)
    best_combos = df_results.loc[
        df_results.groupby(["symbol", "strategy"])["sharpe"].idxmax()
    ].sort_values(["symbol", "sharpe"], ascending=[True, False])
    for _, r in best_combos.iterrows():
        print(f"{r['symbol']:>8} {r['strategy']:>22} {r['variant']:>10} {r['total_return_pct']:>+8.2f} {r['sharpe']:>+7.2f} {r['max_drawdown_pct']:>+8.2f} {int(r['n_position_flips']):>6}")

    # Best per symbol overall
    print()
    print("=" * 100)
    print("Best (strategy, variant) per symbol:")
    print("=" * 100)
    best_per_sym = df_results.loc[df_results.groupby("symbol")["sharpe"].idxmax()]
    for _, r in best_per_sym.iterrows():
        print(f"  {r['symbol']:>8}  {r['strategy']:>22} + {r['variant']:>10}  Sharpe={r['sharpe']:+.2f}  ret={r['total_return_pct']:+6.2f}%  maxDD={r['max_drawdown_pct']:+6.2f}%")

    # Variant analysis (across all symbols × strategies)
    print()
    print("=" * 80)
    print("Variant impact (average across symbols × strategies):")
    print("=" * 80)
    by_variant = df_results.groupby("variant").agg(
        avg_return=("total_return_pct", "mean"),
        avg_sharpe=("sharpe", "mean"),
        avg_dd=("max_drawdown_pct", "mean"),
        avg_flips=("n_position_flips", "mean"),
    ).sort_values("avg_sharpe", ascending=False)
    for v, r in by_variant.iterrows():
        print(f"  {v:>12s}  ret={r['avg_return']:+6.2f}%  Sharpe={r['avg_sharpe']:+5.2f}  DD={r['avg_dd']:+6.2f}%  flips={r['avg_flips']:.0f}")

    # JSON summary
    summary = {
        "best_per_symbol": best_per_sym.to_dict("records"),
        "best_per_strategy": best_combos.to_dict("records"),
        "variant_avg": by_variant.reset_index().to_dict("records"),
        "results": rows,
    }
    summary_path = ROOT / "data" / f"backtest_crypto_sltp_{args.days}d_{ts}_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n[saved] summary: {summary_path}")


if __name__ == "__main__":
    main()
