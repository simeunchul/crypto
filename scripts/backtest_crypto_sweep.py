"""Crypto multi-strategy backtest sweep — parameterized.

CLI:
  --days 21 --interval 1h           : 짧은 1h backtest (default, L/S 포함)
  --days 60 --interval 1h --no-ls   : 60일 1h, L/S strategies 제외
  --days 365 --interval 1d --no-ls  : 1년 daily backtest

Output:
  data/backtest_crypto_sweep_<TAG>_<TS>.parquet
  data/backtest_crypto_sweep_<TAG>_<TS>_summary.json
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
from autotrader.data.crypto_signals import (
    fetch_funding_rate, fetch_long_short_ratio,
)
from autotrader.backtest.crypto_strategies import (
    BacktestConfig, STRATEGIES, backtest, summarize, merge_signals,
)


# CCXT symbol → Binance API symbol 매핑 (signals fetch 용)
SYMBOL_MAP = {
    "BTC/USDT:USDT": "BTCUSDT",
    "ETH/USDT:USDT": "ETHUSDT",
    "SOL/USDT:USDT": "SOLUSDT",
    "AVAX/USDT:USDT": "AVAXUSDT",
    "BNB/USDT:USDT": "BNBUSDT",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--interval", default="1h",
                    help="1h, 5m, 1d 등 CCXT timeframe")
    ap.add_argument("--no-ls", action="store_true",
                    help="L/S ratio 제외 (longer history 위해)")
    ap.add_argument("--tag", default=None,
                    help="output filename tag (default: <interval>_<days>d)")
    args = ap.parse_args()

    tag = args.tag or f"{args.interval}_{args.days}d"
    if args.no_ls:
        tag += "_nols"

    SYMBOLS = list(SYMBOL_MAP.keys())

    # bars_per_year 조정 (annualized Sharpe 정확하게)
    if args.interval == "1d":
        bars_per_year = 365
    elif args.interval == "1h":
        bars_per_year = 8760
    elif args.interval == "5m":
        bars_per_year = 105120
    else:
        bars_per_year = 8760

    # MA periods 조정 (interval 에 따라)
    if args.interval == "1d":
        cfg = BacktestConfig(fast_ma=6, slow_ma=24, bb_window=20, bb_std=2.0,
                              bars_per_year=bars_per_year)
    else:
        cfg = BacktestConfig(bars_per_year=bars_per_year)

    print(f"=== Sweep [{tag}] : {args.days}d × {args.interval} ===")
    print(f"  fast_ma={cfg.fast_ma} slow_ma={cfg.slow_ma}  bb={cfg.bb_window}/{cfg.bb_std}")
    print(f"  L/S strategies: {'EXCLUDED' if args.no_ls else 'included'}")

    rows = []
    for ccxt_sym in SYMBOLS:
        binance_sym = SYMBOL_MAP[ccxt_sym]
        print(f"\n=== {ccxt_sym} ===")

        price = fetch_crypto_bars(ccxt_sym, timeframe=args.interval, days=args.days)
        if price.empty:
            print(f"  price empty, skip"); continue

        funding = fetch_funding_rate(binance_sym, days=args.days)
        ls = None if args.no_ls else fetch_long_short_ratio(
            binance_sym, period=args.interval if args.interval == "1h" else "1h",
            days=min(args.days, 30),
        )

        df = merge_signals(price, funding, ls)
        # NaN 처리: funding 만 필수, L/S 는 옵션
        if "funding_rate" in df.columns:
            df = df.dropna(subset=["funding_rate"])
        if not args.no_ls and "long_pct" in df.columns:
            df = df.dropna(subset=["long_pct"])
        if df.empty:
            print(f"  merged empty"); continue
        print(f"  data: {len(df):>4} bars  {df.index.min()} ~ {df.index.max()}")

        # Strategy filter (L/S 제외 시)
        active_strategies = STRATEGIES.copy()
        if args.no_ls:
            active_strategies.pop("E_trend+lsratio", None)
            active_strategies.pop("F_trend+3factor", None)

        for strat_name, strat_fn in active_strategies.items():
            try:
                pos = strat_fn(df, cfg)
                out = backtest(df, pos, cfg)
                s = summarize(out, cfg)
                row = {"symbol": binance_sym, "strategy": strat_name, **s}
                rows.append(row)
            except Exception as e:
                print(f"    [{strat_name}] FAIL: {e}")

    if not rows:
        print("\nNo results.")
        return

    df_results = pd.DataFrame(rows)
    df_results = df_results.sort_values(["symbol", "sharpe"], ascending=[True, False])

    # Save
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = ROOT / "data" / f"backtest_crypto_sweep_{tag}_{ts}.parquet"
    df_results.to_parquet(out_path)
    print(f"\n[saved] {out_path}")

    # Console summary
    print()
    print("=" * 95)
    print(f"{'symbol':>8} {'strategy':>22} {'return%':>8} {'Sharpe':>7} {'maxDD%':>8} {'flips':>6} {'cost%':>6} {'win%':>6}")
    print("=" * 95)
    cur_sym = None
    for _, r in df_results.iterrows():
        if r["symbol"] != cur_sym:
            if cur_sym is not None:
                print("-" * 95)
            cur_sym = r["symbol"]
        print(f"{r['symbol']:>8} {r['strategy']:>22} {r['total_return_pct']:>+8.2f} {r['sharpe']:>+7.2f} {r['max_drawdown_pct']:>+8.2f} {int(r['n_position_flips']):>6} {r['total_cost_pct']:>6.2f} {r['win_rate']*100:>5.1f}")

    # Best per symbol
    print()
    print("=" * 60)
    print("Best strategy per symbol (by Sharpe):")
    print("=" * 60)
    best = df_results.groupby("symbol").first().reset_index()
    for _, r in best.iterrows():
        print(f"  {r['symbol']:>8}  {r['strategy']:>22}  Sharpe={r['sharpe']:+.2f}  return={r['total_return_pct']:+.2f}%")

    # Aggregate per strategy
    print()
    print("=" * 60)
    print("Mean stats per strategy (across all symbols):")
    print("=" * 60)
    by_strat = df_results.groupby("strategy").agg(
        avg_return=("total_return_pct", "mean"),
        avg_sharpe=("sharpe", "mean"),
        avg_dd=("max_drawdown_pct", "mean"),
        avg_flips=("n_position_flips", "mean"),
    ).sort_values("avg_sharpe", ascending=False)
    for strat, r in by_strat.iterrows():
        print(f"  {strat:>22}  ret={r['avg_return']:+6.2f}%  Sharpe={r['avg_sharpe']:+5.2f}  DD={r['avg_dd']:+6.2f}%  flips={r['avg_flips']:.0f}")

    # JSON summary
    summary = {
        "results": rows,
        "best_per_symbol": best.to_dict("records"),
        "mean_per_strategy": by_strat.reset_index().to_dict("records"),
    }
    summary_path = ROOT / "data" / f"backtest_crypto_sweep_{tag}_{ts}_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n[saved] summary: {summary_path}")


if __name__ == "__main__":
    main()
