"""코인 횡보 전략 탐구 — 횡보 구간만 분리해서 어떤 전략이 먹는가.

질문 (사용자): "코인 횡보 전략을 별도로 파보고 싶다."

어제 한계: trend vs MR 을 전체 기간(추세 포함)에서 비교 → MR 이 추세에 깔려 손해.
이번 접근: ADX 로 횡보 구간(추세 약함)만 떼어내서, 그 구간에서만 전략별 성과 비교.
          → "코인 횡보장 자체가 먹을 수 있나" 의 상한선 (사후 횡보 식별 기준).

전략 비교 (4h봉):
  trend       : long-short MA 12/48 + trail2 (현 봇, 비교 기준)
  bollinger_mr: 20/2σ 밴드 상단 SHORT / 하단 LONG / 중심 복귀 청산
  rsi_mr      : RSI<30 LONG / >70 SHORT / 50 복귀 청산
  grid_linear : 중심선 대비 편차 비례 역포지션 (grid 의 연속 근사)

regime 식별: ADX(14). ADX<20 = 횡보, >25 = 추세.
성과 측정: 전체 / 횡보 구간만 / 추세 구간만 각각.
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


# ──────────────────────────────────────── 지표
def compute_adx(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Wilder ADX — 추세 강도 (방향 무관). >25 추세, <20 횡보."""
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / window, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / window, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / window, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / window, adjust=False).mean()
    return adx.fillna(0)


def rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    ag = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    al = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = ag / al.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


# ──────────────────────────────────────── 횡보 전략들
def strat_rsi_mr(df: pd.DataFrame, low_th=30, high_th=70) -> pd.Series:
    """RSI mean reversion. RSI<low → LONG, >high → SHORT, 50 복귀 → 청산."""
    r = rsi(df["close"], 14)
    pos = pd.Series(0.0, index=df.index)
    state = 0
    for i in range(len(df)):
        rv = r.iloc[i]
        if state == 0:
            if rv < low_th:
                state = 1
            elif rv > high_th:
                state = -1
        elif state == 1 and rv >= 50:
            state = 0
        elif state == -1 and rv <= 50:
            state = 0
        pos.iloc[i] = state
    return pos.shift(1).fillna(0)


def strat_grid_linear(df: pd.DataFrame, window=20, scale=2.0) -> pd.Series:
    """Grid 의 연속 근사 — 중심선(20MA) 대비 편차 비례 역포지션.
    가격이 평균보다 낮으면 LONG, 높으면 SHORT, 편차 클수록 포지션 큼 (-1~+1 clip).
    """
    ma = df["close"].rolling(window).mean()
    sd = df["close"].rolling(window).std()
    z = (df["close"] - ma) / sd.replace(0, np.nan)   # z-score
    pos = (-z / scale).clip(-1, 1)   # 평균 위 → SHORT, 아래 → LONG
    return pos.shift(1).fillna(0)


def regime_perf(out: pd.DataFrame, mask: pd.Series) -> dict:
    """mask=True 인 bar 들만 골라 성과 측정 (구간 비연속이라 단순 누적)."""
    sub = out[mask]
    if len(sub) < 5 or sub["net_ret"].std() == 0:
        return {"n_bars": len(sub), "ret_pct": 0.0, "sharpe": float("nan")}
    rets = sub["net_ret"]
    cum = float((1 + rets).prod() - 1) * 100
    sharpe = float(rets.mean() / rets.std() * np.sqrt(BARS_PER_YEAR_4H))
    return {"n_bars": int(len(sub)), "ret_pct": cum, "sharpe": sharpe}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--adx-range", type=float, default=20, help="ADX 이하 = 횡보")
    ap.add_argument("--adx-trend", type=float, default=25, help="ADX 이상 = 추세")
    args = ap.parse_args()

    cfg = BacktestConfig(fast_ma=12, slow_ma=48, bb_window=20, bb_std=2.0,
                         cost_bps_per_side=4.0, bars_per_year=BARS_PER_YEAR_4H)

    print(f"=== 코인 횡보 전략 탐구 — {args.days}d, ADX<{args.adx_range} 횡보 / >{args.adx_trend} 추세 ===")

    strategies = {
        "trend":        lambda df: apply_risk_overlay(df, strat_trend(df, cfg), trailing_stop_pct=0.02),
        "bollinger_mr": lambda df: strat_bollinger_mr(df, cfg),
        "rsi_mr":       lambda df: strat_rsi_mr(df),
        "grid_linear":  lambda df: strat_grid_linear(df),
    }

    rows = []
    total_range_bars = 0
    total_bars = 0
    for ccxt_sym, binance_sym in SYMBOLS:
        try:
            df = fetch_crypto_bars(ccxt_sym, timeframe="4h", days=args.days)
        except Exception as e:
            print(f"  {binance_sym}: fetch fail {e}"); continue
        if df.empty or len(df) < 200:
            print(f"  {binance_sym}: insufficient"); continue

        adx = compute_adx(df, 14)
        range_mask = adx < args.adx_range
        trend_mask = adx > args.adx_trend
        total_range_bars += int(range_mask.sum())
        total_bars += len(df)

        for sname, fn in strategies.items():
            pos = fn(df)
            out = backtest(df, pos, cfg)
            all_p = regime_perf(out, pd.Series(True, index=df.index))
            rng_p = regime_perf(out, range_mask)
            trd_p = regime_perf(out, trend_mask)
            rows.append({
                "symbol": binance_sym, "strategy": sname,
                "all_ret": all_p["ret_pct"], "all_sharpe": all_p["sharpe"],
                "range_ret": rng_p["ret_pct"], "range_sharpe": rng_p["sharpe"], "range_bars": rng_p["n_bars"],
                "trend_ret": trd_p["ret_pct"], "trend_sharpe": trd_p["sharpe"], "trend_bars": trd_p["n_bars"],
            })
        print(f"  {binance_sym}: {len(df)} bars, 횡보 {int(range_mask.sum())} ({range_mask.mean()*100:.0f}%)")

    if not rows:
        print("No results."); return

    df_res = pd.DataFrame(rows)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = ROOT / "data" / f"backtest_crypto_range_{args.days}d_{ts}.parquet"
    df_res.to_parquet(out_path)
    print(f"\n[saved] {out_path}")
    print(f"  전체 중 횡보 비율: {total_range_bars/total_bars*100:.1f}%")

    # 전략 × regime 평균
    print(f"\n{'='*95}")
    print(f"전략별 성과 — 전체 / 횡보 구간만 / 추세 구간만 (10종 평균)")
    print(f"{'='*95}")
    print(f"{'strategy':>14} | {'전체_ret%':>9} {'전체_Sh':>8} | {'횡보_ret%':>9} {'횡보_Sh':>8} | {'추세_ret%':>9} {'추세_Sh':>8}")
    print("-" * 95)
    g = df_res.groupby("strategy")
    summary = []
    for sname in ["trend", "bollinger_mr", "rsi_mr", "grid_linear"]:
        if sname not in g.groups:
            continue
        gg = g.get_group(sname)
        row = {
            "strategy": sname,
            "all_ret": gg["all_ret"].mean(), "all_sharpe": gg["all_sharpe"].mean(),
            "range_ret": gg["range_ret"].mean(), "range_sharpe": gg["range_sharpe"].mean(),
            "trend_ret": gg["trend_ret"].mean(), "trend_sharpe": gg["trend_sharpe"].mean(),
        }
        summary.append(row)
        print(f"{sname:>14} | {row['all_ret']:>+9.2f} {row['all_sharpe']:>+8.2f} | "
              f"{row['range_ret']:>+9.2f} {row['range_sharpe']:>+8.2f} | "
              f"{row['trend_ret']:>+9.2f} {row['trend_sharpe']:>+8.2f}")

    # 횡보 구간 승자
    print(f"\n{'='*70}")
    print(f"횡보 구간 (ADX<{args.adx_range}) 승자")
    print(f"{'='*70}")
    range_sorted = sorted(summary, key=lambda r: r["range_ret"], reverse=True)
    for i, r in enumerate(range_sorted):
        tag = " ← 횡보 1위" if i == 0 else ""
        print(f"  {r['strategy']:>14}: 횡보 {r['range_ret']:+.2f}% (Sharpe {r['range_sharpe']:+.2f}){tag}")

    best_range = range_sorted[0]
    trend_range = next(r for r in summary if r["strategy"] == "trend")
    print(f"\n{'='*70}")
    print(f"결론")
    print(f"{'='*70}")
    if best_range["strategy"] != "trend" and best_range["range_ret"] > trend_range["range_ret"]:
        print(f"  ✅ 횡보 구간에선 {best_range['strategy']}({best_range['range_ret']:+.2f}%) > trend({trend_range['range_ret']:+.2f}%)")
        print(f"     → 코인 횡보장 전용 전략으로 {best_range['strategy']} 검토 가치 있음")
    else:
        print(f"  ❌ 횡보 구간에서도 trend({trend_range['range_ret']:+.2f}%) 가 횡보 전략들 이상")
        print(f"     → 코인은 횡보 구간조차 trend-follow 가 나음 (또는 횡보 전략 추가 튜닝 필요)")

    summary_path = ROOT / "data" / f"backtest_crypto_range_{args.days}d_{ts}_summary.json"
    summary_path.write_text(json.dumps({"params": vars(args),
                                        "range_pct": total_range_bars/total_bars*100,
                                        "summary": summary}, indent=2, default=str), encoding="utf-8")
    print(f"\n[saved] {summary_path}")


if __name__ == "__main__":
    main()
