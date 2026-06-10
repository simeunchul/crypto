"""옵션 Y backtest — 5m polling + 4h closed-bar 신호 + trail-only + 차단 플래그.

질문:
  "어제 live 에서 발생한 휩쏘 (24h에 94 trade) 가 옵션 Y로 정말 사라지는가?
   그리고 기존 backtest 결과 (Sharpe +6.29) 가 옵션 Y 적용 후 유지/개선되는가?"

3가지 모드 비교:
  Y (옵션 Y, 정석)   : 5m bar 단위 진행 + 4h closed-bar signal + trail + 차단
  L (live 어제 동작)  : 5m bar 단위 진행 + 5m polling 마다 4h MA 재계산 + 차단 없음
  B (기존 4h backtest): 4h bar-by-bar (원래 sweep 방식)

Output:
  - data/backtest_crypto_optY_<days>d_<ts>.parquet
  - 콘솔: 종목별 + 평균 비교표
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
    BacktestConfig, apply_risk_overlay, backtest as bt_engine, summarize, strat_trend,
)


# 운영봇 실제 종목 (TRX 제외 18종 중 대표 10종)
SYMBOLS = [
    ("BTC/USDT:USDT",  "BTCUSDT"),
    ("ETH/USDT:USDT",  "ETHUSDT"),
    ("SOL/USDT:USDT",  "SOLUSDT"),
    ("AVAX/USDT:USDT", "AVAXUSDT"),
    ("BNB/USDT:USDT",  "BNBUSDT"),
    ("DOGE/USDT:USDT", "DOGEUSDT"),
    ("OP/USDT:USDT",   "OPUSDT"),
    ("SUI/USDT:USDT",  "SUIUSDT"),
    ("NEAR/USDT:USDT", "NEARUSDT"),
    ("INJ/USDT:USDT",  "INJUSDT"),
]


def make_4h_closed_signal(df_4h: pd.DataFrame, fast: int = 12, slow: int = 48,
                          long_only: bool = True) -> pd.Series:
    """closed 4h bar 만으로 MA → 신호. 1-bar lag (다음 bar 부터 사용 — backtest convention)."""
    f = df_4h["close"].rolling(fast).mean()
    s = df_4h["close"].rolling(slow).mean()
    if long_only:
        sig = (f > s).astype(int)
    else:
        sig = np.where(f > s, 1, np.where(f < s, -1, 0)).astype(int)
        sig = pd.Series(sig, index=df_4h.index)
    return sig.shift(1).fillna(0)


def make_inprogress_4h_signal(df_5m: pd.DataFrame, fast_h: int = 12, slow_h: int = 48) -> pd.Series:
    """live 어제 동작 시뮬: 매 5m bar 마다 직전 fast_h × 4h / slow_h × 4h 의 close 평균으로 MA 재계산.

    즉 4h 진행 중인 봉의 잠정 close 도 포함된 동작.

    구현: 5m bar 단위로 직전 N개 5m bar 의 close 평균 (fast/slow MA 의 5m bar 환산).
      fast_h = 12 (4h bar) = 12 × (4h/5m) = 12 × 48 = 576 (5m bar)
      slow_h = 48 (4h bar) = 48 × 48 = 2304 (5m bar)
    """
    fast_5m = fast_h * 48
    slow_5m = slow_h * 48
    f = df_5m["close"].rolling(fast_5m).mean()
    s = df_5m["close"].rolling(slow_5m).mean()
    sig = (f > s).astype(int)
    return sig.shift(1).fillna(0)


def simulate_5min_trail(
    df_5m: pd.DataFrame,
    signal_series: pd.Series,
    trail_pct: float = 0.02,
    taker_fee_bps: float = 4.0,
    blocked: bool = True,
    trail_on_bar_close_only: bool = False,
    bar_close_idx_set: set | None = None,
) -> tuple[dict, pd.DataFrame]:
    """5m bar 단위 시뮬레이션 + trail-only.

    Args:
      df_5m: 5분 봉 DataFrame (close, high, low 컬럼)
      signal_series: 5m bar 시점에 align 된 진입 신호 (0/1, shift 적용된 상태)
      trail_pct: trailing stop 폭
      taker_fee_bps: 시장가 수수료 (bps, per side)
      blocked: True 면 옵션 Y (차단 적용), False 면 어제 live (차단 없음)
    """
    sig_arr = signal_series.values.astype(np.int8)
    # live 봇은 mark_price (= 그 polling 순간의 close) 만 사용 — bar 안 spike 무시.
    # backtest 도 close 만 써서 live 와 동일 동작 보장.
    close_arr = df_5m["close"].values
    n = len(df_5m)

    positions = np.zeros(n, dtype=np.int8)
    state = 0
    entry_price = None
    high_water = None
    stopped_block = 0

    n_open = 0
    n_trail = 0

    for i in range(n):
        sig = int(sig_arr[i])
        c = close_arr[i]

        # 1. trail check — close 만 사용 (live 와 동일)
        #    옵션 Z: trail_on_bar_close_only=True 면 4h 봉 마감 시점에만 평가
        eval_trail = state == 1 and entry_price is not None
        if trail_on_bar_close_only and bar_close_idx_set is not None:
            eval_trail = eval_trail and (i in bar_close_idx_set)
        if eval_trail:
            if c > high_water:
                high_water = c
            stop_line = high_water * (1 - trail_pct)
            if c <= stop_line:
                state = 0
                entry_price = None
                high_water = None
                if blocked:
                    stopped_block = 1
                n_trail += 1

        # 2. 차단 해제 (신호가 바뀌면)
        if blocked and sig != stopped_block:
            stopped_block = 0

        # 3. 진입
        if state == 0 and sig == 1:
            if not blocked or stopped_block != 1:
                state = 1
                entry_price = c
                high_water = c
                n_open += 1

        positions[i] = state

    df = df_5m.copy()
    df["position"] = positions
    df["ret"] = df["close"].pct_change().fillna(0)
    # 1-bar lag (이전 bar 의 position 으로 현재 bar 수익 적용)
    df["pos_ret"] = pd.Series(positions, index=df.index).shift(1).fillna(0) * df["ret"]
    df["pos_change"] = pd.Series(positions, index=df.index).diff().abs().fillna(0)
    cost_per_side = taker_fee_bps / 10_000.0
    df["cost"] = df["pos_change"] * cost_per_side
    df["net_ret"] = df["pos_ret"] - df["cost"]
    df["equity"] = (1 + df["net_ret"]).cumprod() * 10_000.0

    bars_per_year = 365 * 24 * 12   # 5m / year
    rets = df["net_ret"]
    final_eq = float(df["equity"].iloc[-1])
    total_ret = (final_eq / 10_000.0) - 1
    sharpe = float(rets.mean() / rets.std() * np.sqrt(bars_per_year)) if rets.std() > 0 else float("nan")
    max_dd = float(((df["equity"] / df["equity"].cummax()) - 1).min())

    return {
        "n_open": n_open,
        "n_trail": n_trail,
        "n_trades": n_open + n_trail,
        "total_return_pct": float(total_ret * 100),
        "sharpe": sharpe,
        "max_drawdown_pct": float(max_dd * 100),
        "n_bars": n,
        "duration_days": float(n / (24 * 12)),
        "final_equity": final_eq,
    }, df


def run_baseline_4h_bar(df_4h: pd.DataFrame, trail_pct: float, fee_bps: float = 4.0) -> dict:
    """기존 4h bar-by-bar backtest (sweep 방식과 동일)."""
    cfg = BacktestConfig(fast_ma=12, slow_ma=48, cost_bps_per_side=fee_bps,
                         bars_per_year=2190)
    base_pos = strat_trend(df_4h, cfg)
    pos = apply_risk_overlay(df_4h, base_pos, trailing_stop_pct=trail_pct)
    out = bt_engine(df_4h, pos, cfg)
    s = summarize(out, cfg)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30,
                    help="백테스트 기간. 5m bar 365d = ~10만 행 × 종목 — 메모리/시간 큼.")
    ap.add_argument("--trail-pct", type=float, default=0.02)
    ap.add_argument("--fast", type=int, default=12, help="4h bar 단위 fast MA")
    ap.add_argument("--slow", type=int, default=48, help="4h bar 단위 slow MA")
    args = ap.parse_args()

    print(f"=== Option Y backtest (5m polling) — {args.days}d, trail={args.trail_pct:.2%} ===")
    print(f"  10 symbols × 3 modes (Y blocked / L unblocked / B 4h baseline)")

    rows = []
    for ccxt_sym, binance_sym in SYMBOLS:
        print(f"\n--- {binance_sym} ---")

        try:
            df_4h = fetch_crypto_bars(ccxt_sym, timeframe="4h", days=args.days)
        except Exception as e:
            print(f"  4h fetch fail: {e}"); continue
        if df_4h.empty or len(df_4h) < args.slow + 5:
            print(f"  4h insufficient ({len(df_4h)} bars)"); continue

        try:
            df_5m = fetch_crypto_bars(ccxt_sym, timeframe="5m", days=args.days)
        except Exception as e:
            print(f"  5m fetch fail: {e}"); continue
        if df_5m.empty:
            print(f"  5m empty"); continue

        print(f"  4h bars: {len(df_4h)}, 5m bars: {len(df_5m)}")

        # 1. 신호 만들기
        sig_4h_closed = make_4h_closed_signal(df_4h, args.fast, args.slow)
        sig_4h_for_5m = sig_4h_closed.reindex(df_5m.index, method="ffill").fillna(0).astype(int)
        sig_inprogress = make_inprogress_4h_signal(df_5m, args.fast, args.slow)

        # 4h 봉 마감 시점의 5m bar index 집합 (옵션 Z 용)
        # df_4h.index 의 timestamp 가 df_5m.index 에 정확히 매핑되는 위치
        bar_close_positions = df_5m.index.searchsorted(df_4h.index, side="left")
        bar_close_positions = bar_close_positions[bar_close_positions < len(df_5m)]
        bar_close_idx_set = set(bar_close_positions.tolist())

        # 2. 옵션 Y (closed signal + 차단, trail 매 5m)
        res_Y, _ = simulate_5min_trail(df_5m, sig_4h_for_5m, args.trail_pct, blocked=True,
                                        trail_on_bar_close_only=False)
        # 3. 옵션 Z (closed signal + 차단 + trail 도 4h 마감만)
        res_Z, _ = simulate_5min_trail(df_5m, sig_4h_for_5m, args.trail_pct, blocked=True,
                                        trail_on_bar_close_only=True,
                                        bar_close_idx_set=bar_close_idx_set)
        # 4. live 어제 (진행 중 봉 신호 + 차단 없음, trail 매 5m)
        res_L, _ = simulate_5min_trail(df_5m, sig_inprogress, args.trail_pct, blocked=False,
                                        trail_on_bar_close_only=False)
        # 5. 4h bar baseline
        res_B = run_baseline_4h_bar(df_4h, args.trail_pct)

        print(f"  Y (옵션 Y, 매 5m trail)  : ret={res_Y['total_return_pct']:+8.2f}%  Sharpe={res_Y['sharpe']:+5.2f}  DD={res_Y['max_drawdown_pct']:+6.2f}%  open={res_Y['n_open']:>3}  trail={res_Y['n_trail']:>3}")
        print(f"  Z (옵션 Z, 4h trail) ★   : ret={res_Z['total_return_pct']:+8.2f}%  Sharpe={res_Z['sharpe']:+5.2f}  DD={res_Z['max_drawdown_pct']:+6.2f}%  open={res_Z['n_open']:>3}  trail={res_Z['n_trail']:>3}")
        print(f"  L (live 어제)            : ret={res_L['total_return_pct']:+8.2f}%  Sharpe={res_L['sharpe']:+5.2f}  DD={res_L['max_drawdown_pct']:+6.2f}%  open={res_L['n_open']:>3}  trail={res_L['n_trail']:>3}")
        print(f"  B (4h bar baseline)      : ret={res_B['total_return_pct']:+8.2f}%  Sharpe={res_B['sharpe']:+5.2f}  DD={res_B['max_drawdown_pct']:+6.2f}%  trades={int(res_B['n_position_flips'])}")

        rows.append({"symbol": binance_sym, "mode": "Y_5m_trail",   **res_Y})
        rows.append({"symbol": binance_sym, "mode": "Z_bar_trail",  **res_Z})
        rows.append({"symbol": binance_sym, "mode": "L_unblocked",  **res_L})
        rows.append({"symbol": binance_sym, "mode": "B_4h_bar",
                     "n_open": int(res_B["n_position_flips"]),
                     "n_trail": 0,
                     "n_trades": int(res_B["n_position_flips"]),
                     "total_return_pct": res_B["total_return_pct"],
                     "sharpe": res_B["sharpe"],
                     "max_drawdown_pct": res_B["max_drawdown_pct"],
                     "n_bars": res_B["n_bars"],
                     "duration_days": res_B["duration_days"],
                     "final_equity": res_B["final_equity"]})

    if not rows:
        print("\nNo results."); return

    df_res = pd.DataFrame(rows).dropna(subset=["sharpe"])

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_parquet = ROOT / "data" / f"backtest_crypto_optY_{args.days}d_{ts}.parquet"
    df_res.to_parquet(out_parquet)
    print(f"\n[saved] {out_parquet}")

    # 비교표
    print(f"\n{'='*110}")
    print(f"비교 — 종목 × 모드")
    print(f"{'='*110}")
    print(f"{'symbol':>10} {'mode':>14} {'ret%':>10} {'Sharpe':>8} {'DD%':>8} {'open':>6} {'trail':>6} {'trades':>7}")
    print("-" * 110)
    for _, r in df_res.sort_values(["symbol", "mode"]).iterrows():
        print(f"{r['symbol']:>10} {r['mode']:>14} "
              f"{r['total_return_pct']:>+10.2f} {r['sharpe']:>+8.2f} {r['max_drawdown_pct']:>+8.2f} "
              f"{int(r['n_open']):>6} {int(r['n_trail']):>6} {int(r['n_trades']):>7}")

    # 평균
    print(f"\n{'='*80}")
    print(f"평균 (전 종목)")
    print(f"{'='*80}")
    print(f"{'mode':>14} {'avg_ret%':>10} {'avg_Sharpe':>11} {'avg_DD%':>9} {'avg_trades':>11}")
    print("-" * 80)
    g = df_res.groupby("mode").agg(
        avg_ret=("total_return_pct", "mean"),
        avg_sharpe=("sharpe", "mean"),
        avg_dd=("max_drawdown_pct", "mean"),
        avg_trades=("n_trades", "mean"),
    )
    for mode, r in g.iterrows():
        print(f"{mode:>14} {r['avg_ret']:>+10.2f} {r['avg_sharpe']:>+11.2f} {r['avg_dd']:>+9.2f} {r['avg_trades']:>11.0f}")

    # JSON summary
    summary = {
        "params": {"days": args.days, "trail_pct": args.trail_pct,
                   "fast": args.fast, "slow": args.slow,
                   "symbols": [s[1] for s in SYMBOLS]},
        "results": rows,
        "averages": g.to_dict("index"),
    }
    summary_path = ROOT / "data" / f"backtest_crypto_optY_{args.days}d_{ts}_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n[saved] {summary_path}")


if __name__ == "__main__":
    main()
