"""Trail 평가 주기 비교 — 실시간(5m) vs 1h vs 4h (옵션 2 vs 3 vs Z).

질문 (사용자): "옵션 2 (실시간 5분 trail) 로 하고 싶다. 옵션 3 (1h trail) 도 테스트.
              매도가 의도대로(고점 -2%) 끊기는지 보고 싶다."

설정:
  - 신호: 4h 봉 마감 close, long-short (+1/-1), 1-bar lag
  - trail 청산: 평가 주기만 다름
      5m (옵션 2, 실시간)  : 매 5분 bar
      1h (옵션 3)          : 1시간 봉 마감 (5m bar 12개마다)
      4h (옵션 Z, 현재)    : 4시간 봉 마감 (5m bar 48개마다)
  - 차단 플래그 (trail 후 같은 방향 신호 재진입 금지)

핵심 지표:
  - 수익 / Sharpe / DD / trade 횟수 (휩쏘 정도)
  - avg_exit_drawdown: 청산 시 실제 되돌림폭 (peak 대비). -2% 에 가까울수록 의도대로.

전체 60일 비교 + 20일 윈도우 walk-forward.
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

BARS_PER_YEAR_5M = 365 * 24 * 12   # 105120
BARS_PER_4H_IN_5M = 48             # 4h = 48 × 5m
BARS_PER_1H_IN_5M = 12


def make_4h_signal_longshort(df_4h: pd.DataFrame, fast: int, slow: int) -> pd.Series:
    """closed 4h bar long-short 신호 (+1/-1), 1-bar lag."""
    f = df_4h["close"].rolling(fast).mean()
    s = df_4h["close"].rolling(slow).mean()
    sig = np.where(f > s, 1, np.where(f < s, -1, 0))
    return pd.Series(sig, index=df_4h.index).shift(1).fillna(0)


def simulate(df_5m, sig_aligned, trail_pct, eval_every, taker_fee_bps=4.0):
    """5m bar 단위 long-short trail 시뮬.

    Args:
      eval_every: trail 평가 주기 (5m bar 단위). 1=실시간 5m, 12=1h, 48=4h.
    """
    close = df_5m["close"].values
    sig = sig_aligned.values.astype(np.int8)
    n = len(df_5m)

    pos = np.zeros(n, dtype=np.int8)
    state = 0
    entry_price = None
    high_water = None
    low_water = None
    stopped_block = 0

    n_open = 0
    n_trail = 0
    exit_drawdowns = []   # 청산 시 peak 대비 되돌림폭 (%)

    for i in range(n):
        c = close[i]
        s = int(sig[i])

        # trail 평가 (eval_every 주기에만 high_water 갱신 + 체크)
        if state != 0 and entry_price is not None and (i % eval_every == 0):
            stopped = False
            if state == 1:
                high_water = max(high_water, c)
                if c < high_water * (1 - trail_pct):
                    stopped = True
                    exit_drawdowns.append((c - high_water) / high_water * 100)
            elif state == -1:
                low_water = min(low_water, c)
                if c > low_water * (1 + trail_pct):
                    stopped = True
                    exit_drawdowns.append((low_water - c) / low_water * 100)
            if stopped:
                stopped_block = state
                state = 0
                entry_price = high_water = low_water = None
                n_trail += 1

        # 신호 진입 — 4h 봉 마감 시점 (5m bar 48개마다) + 차단
        is_4h_close = (i % BARS_PER_4H_IN_5M == 0)
        if is_4h_close and state == 0:
            if s != stopped_block:
                stopped_block = 0
            if s != 0 and s != stopped_block:
                state = s
                entry_price = c
                high_water = c if s == 1 else None
                low_water = c if s == -1 else None
                n_open += 1

        pos[i] = state

    df = df_5m.copy()
    df["position"] = pos
    df["ret"] = df["close"].pct_change().fillna(0)
    df["pos_ret"] = pd.Series(pos, index=df.index).shift(1).fillna(0) * df["ret"]
    df["pos_change"] = pd.Series(pos, index=df.index).diff().abs().fillna(0)
    df["cost"] = df["pos_change"] * (taker_fee_bps / 10_000.0)
    df["net_ret"] = df["pos_ret"] - df["cost"]

    rets = df["net_ret"]
    eq = (1 + rets).cumprod()
    total_ret = float(eq.iloc[-1] - 1) * 100 if len(eq) else 0.0
    sharpe = float(rets.mean() / rets.std() * np.sqrt(BARS_PER_YEAR_5M)) if rets.std() > 0 else float("nan")
    max_dd = float(((eq / eq.cummax()) - 1).min() * 100) if len(eq) else 0.0
    avg_exit_dd = float(np.mean(exit_drawdowns)) if exit_drawdowns else 0.0

    return {
        "n_open": n_open, "n_trail": n_trail, "n_trades": n_open + n_trail,
        "total_return_pct": total_ret, "sharpe": sharpe, "max_dd_pct": max_dd,
        "avg_exit_drawdown_pct": avg_exit_dd,
        "net_ret": df["net_ret"], "position": df["position"],
    }


FREQS = [("5m_realtime", 1), ("1h", BARS_PER_1H_IN_5M), ("4h_optionZ", BARS_PER_4H_IN_5M)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--trail-pct", type=float, default=0.02)
    ap.add_argument("--fast", type=int, default=12)
    ap.add_argument("--slow", type=int, default=48)
    ap.add_argument("--window-days", type=int, default=20)
    args = ap.parse_args()

    print(f"=== Trail 주기 비교 (long-short) — {args.days}d, trail={args.trail_pct:.0%} ===")
    print(f"  5m_realtime (옵션2) / 1h (옵션3) / 4h_optionZ (현재)")

    rows = []
    wf_rows = []
    window_bars_5m = args.window_days * 24 * 12

    for ccxt_sym, binance_sym in SYMBOLS:
        try:
            df_4h = fetch_crypto_bars(ccxt_sym, timeframe="4h", days=args.days)
            df_5m = fetch_crypto_bars(ccxt_sym, timeframe="5m", days=args.days)
        except Exception as e:
            print(f"  {binance_sym}: fetch fail {e}"); continue
        if df_4h.empty or df_5m.empty or len(df_4h) < args.slow + 5:
            print(f"  {binance_sym}: insufficient"); continue

        sig_4h = make_4h_signal_longshort(df_4h, args.fast, args.slow)
        sig_5m = sig_4h.reindex(df_5m.index, method="ffill").fillna(0).astype(int)

        line = f"  {binance_sym:>8}: "
        per_freq = {}
        for fname, ev in FREQS:
            r = simulate(df_5m, sig_5m, args.trail_pct, ev)
            per_freq[fname] = r
            rows.append({"symbol": binance_sym, "freq": fname,
                         "total_return_pct": r["total_return_pct"], "sharpe": r["sharpe"],
                         "max_dd_pct": r["max_dd_pct"], "n_trades": r["n_trades"],
                         "n_trail": r["n_trail"], "avg_exit_drawdown_pct": r["avg_exit_drawdown_pct"]})
            line += f"{fname}={r['total_return_pct']:+.1f}%/Sh{r['sharpe']:+.1f}/x{r['n_trades']}  "
        print(line)

        # walk-forward (윈도우별)
        n = len(df_5m)
        nw = n // window_bars_5m
        for fname, ev in FREQS:
            r = per_freq[fname]
            for w in range(nw):
                lo, hi = w * window_bars_5m, (w + 1) * window_bars_5m
                sub = r["net_ret"].iloc[lo:hi]
                if len(sub) < 2 or sub.std() == 0:
                    continue
                eq = (1 + sub).cumprod()
                wf_rows.append({
                    "symbol": binance_sym, "freq": fname, "window": w,
                    "ret_pct": float((eq.iloc[-1] - 1) * 100),
                    "sharpe": float(sub.mean() / sub.std() * np.sqrt(BARS_PER_YEAR_5M)),
                })

    if not rows:
        print("No results."); return

    df_res = pd.DataFrame(rows)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "data" / f"backtest_crypto_trail_freq_{args.days}d_{ts}.parquet"
    df_res.to_parquet(out)
    print(f"\n[saved] {out}")

    # 전체 평균 비교
    print(f"\n{'='*95}")
    print(f"전체 평균 (10종, {args.days}d) — 주기별 비교")
    print(f"{'='*95}")
    print(f"{'freq':>13} {'avg_ret%':>9} {'avg_Sharpe':>11} {'avg_DD%':>9} {'avg_trades':>11} {'avg_청산되돌림%':>16}")
    print("-" * 95)
    g = df_res.groupby("freq")
    order = ["5m_realtime", "1h", "4h_optionZ"]
    summary = []
    for fname in order:
        if fname not in g.groups:
            continue
        gg = g.get_group(fname)
        row = {
            "freq": fname,
            "avg_ret": gg["total_return_pct"].mean(),
            "avg_sharpe": gg["sharpe"].mean(),
            "avg_dd": gg["max_dd_pct"].mean(),
            "avg_trades": gg["n_trades"].mean(),
            "avg_exit_dd": gg["avg_exit_drawdown_pct"].mean(),
        }
        summary.append(row)
        print(f"{fname:>13} {row['avg_ret']:>+9.2f} {row['avg_sharpe']:>+11.2f} {row['avg_dd']:>+9.2f} {row['avg_trades']:>11.1f} {row['avg_exit_dd']:>+16.2f}")

    # walk-forward 승률
    if wf_rows:
        df_wf = pd.DataFrame(wf_rows)
        print(f"\n{'='*70}")
        print(f"walk-forward ({args.window_days}일 윈도우) — 주기별 승률 / 평균")
        print(f"{'='*70}")
        print(f"{'freq':>13} {'n_win':>6} {'승률%':>8} {'avg_ret%':>10} {'avg_Sharpe':>11}")
        print("-" * 70)
        gw = df_wf.groupby("freq")
        for fname in order:
            if fname not in gw.groups:
                continue
            gg = gw.get_group(fname)
            win = (gg["ret_pct"] > 0).mean() * 100
            print(f"{fname:>13} {len(gg):>6} {win:>8.1f} {gg['ret_pct'].mean():>+10.2f} {gg['sharpe'].mean():>+11.2f}")

    # 결론
    print(f"\n{'='*70}")
    print(f"해석 — 매도 타이밍 (청산 되돌림폭)")
    print(f"{'='*70}")
    for row in summary:
        gap = abs(row["avg_exit_dd"]) - args.trail_pct * 100
        print(f"  {row['freq']:>13}: 평균 {row['avg_exit_dd']:+.2f}% 에서 청산 "
              f"(목표 -{args.trail_pct*100:.0f}% 대비 {gap:+.2f}%p 더 빠짐)")

    summary_path = ROOT / "data" / f"backtest_crypto_trail_freq_{args.days}d_{ts}_summary.json"
    summary_path.write_text(json.dumps({"params": vars(args), "summary": summary},
                                       indent=2, default=str), encoding="utf-8")
    print(f"\n[saved] {summary_path}")


if __name__ == "__main__":
    main()
