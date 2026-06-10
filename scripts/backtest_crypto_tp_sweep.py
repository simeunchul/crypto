"""Take-Profit 룰 스윕 — 현행 trail-only vs 익절 후보 3종.

질문: "AVAX uPnL 이 +$315 → +$238 로 $77 반납했을 때, '봉마감 close' 만 보는
       현 trail 규칙(검증된 옵션 Z) 대신 익절 룰을 넣으면 더 좋아지는가?"

비교 대상:
  - baseline : pure trail 2% (현재 운영봇, +2.76 Sharpe 검증)
  - tiered   : 진입 대비 +N% 이익 도달 후 trail 2% → 1% 로 압축 (winners 일부 보호)
  - partial  : +N% 도달 시 50% 청산, 나머지 trail 2% 유지 (분산↓, 재진입 차단↓)
  - hard_tp  : +N% 도달 시 전량 청산 + signal 반전까지 재진입 차단 (가장 보수적)

세팅 (운영봇 = 검증된 swing 구성):
  - 4h 봉, MA 12/48, long-short, 18종, 365일

각 룰 × 파라미터(N) → 종목별 backtest → 18종 평균 metric.
trail-only baseline 대비 Sharpe / return / max-DD / trades 변화 비교.
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

# Windows cp949 콘솔 대비
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from autotrader.backtest.crypto_strategies import (
    BacktestConfig, backtest, summarize, strat_trend,
)
from autotrader.data.crypto_bars import fetch_crypto_bars

ALT18 = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "AVAX/USDT:USDT", "BNB/USDT:USDT",
    "DOGE/USDT:USDT", "ADA/USDT:USDT", "XRP/USDT:USDT", "DOT/USDT:USDT", "LINK/USDT:USDT",
    "LTC/USDT:USDT", "BCH/USDT:USDT", "ARB/USDT:USDT", "OP/USDT:USDT", "SUI/USDT:USDT",
    "INJ/USDT:USDT", "NEAR/USDT:USDT", "ATOM/USDT:USDT",
]


# ──────────────────────────────────────────────────────────── exit overlays

def _ensure_entry(state, p, last_state, entry_price, hw, lw):
    """state 가 신규로 ±1 이 되면 entry/water 초기화."""
    if state != 0 and last_state == 0:
        entry_price = p
        hw = p if state == 1 else None
        lw = p if state == -1 else None
    return entry_price, hw, lw


def overlay_trail_only(df: pd.DataFrame, base: pd.Series, trail: float = 0.02) -> pd.Series:
    """Baseline — pure trailing stop, signal flip ignored while holding,
    같은 방향 신호로 재진입 차단(trail 후 signal 반전 전까지)."""
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
            blocked = 0  # signal changed → unlock
        out[i] = state
    return pd.Series(out, index=df.index)


def overlay_tiered_trail(df, base, trail_loose=0.02, trail_tight=0.01,
                          tighten_at=0.05):
    """+tighten_at% 이익 도달 후 trail 폭을 trail_tight 로 압축."""
    out = np.zeros(len(df), dtype=float)
    state = 0; entry = None; hw = lw = None; blocked = 0; tightened = False
    closes = df["close"].values; bps = base.fillna(0).astype(int).values
    for i in range(len(df)):
        p = float(closes[i]); bp = int(bps[i])
        stopped = False
        if state != 0 and entry is not None:
            profit_pct = (p - entry) / entry * state  # +이면 이익
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


def overlay_partial_tp(df, base, tp_at=0.10, trail=0.02, partial_frac=0.5):
    """+tp_at% 도달 시 partial_frac 만큼 청산, 나머지는 trail 유지."""
    out = np.zeros(len(df), dtype=float)
    state_sign = 0           # ±1 / 0
    state_size = 0.0         # 현재 노출 비중 (0.0 ~ 1.0)
    entry = None; hw = lw = None; blocked = 0; partial_done = False
    closes = df["close"].values; bps = base.fillna(0).astype(int).values
    keep = 1.0 - partial_frac
    for i in range(len(df)):
        p = float(closes[i]); bp = int(bps[i])
        stopped = False
        if state_sign != 0 and entry is not None:
            profit_pct = (p - entry) / entry * state_sign
            if (not partial_done) and profit_pct >= tp_at:
                state_size *= keep
                partial_done = True
            if state_sign == 1:
                hw = max(hw or p, p)
                if p < hw * (1 - trail): stopped = True
            else:
                lw = min(lw or p, p)
                if p > lw * (1 + trail): stopped = True
        if stopped:
            blocked = state_sign
            state_sign = 0; state_size = 0.0
            entry = None; hw = lw = None; partial_done = False
        if state_sign == 0 and bp != 0 and bp != blocked:
            state_sign = bp; state_size = 1.0; entry = p
            hw = p if bp == 1 else None
            lw = p if bp == -1 else None
            blocked = 0
        elif state_sign == 0 and blocked != 0 and bp != blocked and bp != 0:
            blocked = 0
        out[i] = state_sign * state_size
    return pd.Series(out, index=df.index)


def overlay_hard_tp(df, base, tp_at=0.10, trail=0.02):
    """+tp_at% 도달 시 전량 청산 (도달 안 하면 그대로 trail 도 작동)."""
    out = np.zeros(len(df), dtype=float)
    state = 0; entry = None; hw = lw = None; blocked = 0
    closes = df["close"].values; bps = base.fillna(0).astype(int).values
    for i in range(len(df)):
        p = float(closes[i]); bp = int(bps[i])
        stopped = False
        if state != 0 and entry is not None:
            profit_pct = (p - entry) / entry * state
            if profit_pct >= tp_at:
                stopped = True
            else:
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


# ──────────────────────────────────────────────────────────── sweep

def build_rules():
    """비교할 모든 룰. (이름, overlay 함수, kwargs)."""
    rules = [
        ("baseline_trail2",          overlay_trail_only, {"trail": 0.02}),
    ]
    # Tiered trail — +N% 이익 후 trail 2% → 1% 압축
    for n in (0.03, 0.05, 0.08, 0.10, 0.15):
        rules.append((f"tiered_at+{int(n*100)}%",
                       overlay_tiered_trail,
                       {"trail_loose": 0.02, "trail_tight": 0.01, "tighten_at": n}))
    # Partial TP — +N% 에서 50% 청산
    for n in (0.05, 0.08, 0.10, 0.15):
        rules.append((f"partial50_at+{int(n*100)}%",
                       overlay_partial_tp,
                       {"tp_at": n, "trail": 0.02, "partial_frac": 0.5}))
    # Hard TP — +N% 전량 청산
    for n in (0.05, 0.08, 0.10, 0.15):
        rules.append((f"hard_tp_at+{int(n*100)}%",
                       overlay_hard_tp, {"tp_at": n, "trail": 0.02}))
    return rules


def load_symbol(sym: str, days: int, timeframe: str) -> pd.DataFrame | None:
    """캐시 우선, 모자라면 fetch."""
    try:
        df = fetch_crypto_bars(symbol=sym, timeframe=timeframe, days=days)
    except Exception as e:
        print(f"  [warn] {sym} load fail: {e}")
        return None
    if df is None or df.empty:
        return None
    return df


def run_for_symbol(df: pd.DataFrame, fast: int, slow: int,
                    cfg: BacktestConfig) -> dict[str, dict]:
    """한 종목에 대해 모든 룰을 적용해 summary dict 반환."""
    base = strat_trend(df, BacktestConfig(fast_ma=fast, slow_ma=slow))
    results = {}
    for name, fn, kw in build_rules():
        try:
            pos = fn(df, base, **kw)
            out = backtest(df, pos, cfg)
            s = summarize(out, cfg)
            results[name] = s
        except Exception as e:
            results[name] = {"error": str(e)[:120]}
    return results


def aggregate(per_symbol: dict[str, dict[str, dict]]) -> pd.DataFrame:
    """각 룰 metric 의 18종 mean / median 집계."""
    rows = []
    rule_names = list(next(iter(per_symbol.values())).keys())
    for rule in rule_names:
        sharpes, rets, dds, flips = [], [], [], []
        for sym, by_rule in per_symbol.items():
            s = by_rule.get(rule)
            if not s or "error" in s: continue
            if s.get("sharpe") == s.get("sharpe"):  # not NaN
                sharpes.append(s["sharpe"])
            rets.append(s["total_return_pct"])
            dds.append(s["max_drawdown_pct"])
            flips.append(s["n_position_flips"])
        rows.append({
            "rule": rule,
            "n_symbols": len(sharpes),
            "mean_sharpe": float(np.mean(sharpes)) if sharpes else float("nan"),
            "median_sharpe": float(np.median(sharpes)) if sharpes else float("nan"),
            "mean_return%": float(np.mean(rets)) if rets else float("nan"),
            "mean_maxdd%": float(np.mean(dds)) if dds else float("nan"),
            "mean_flips": float(np.mean(flips)) if flips else float("nan"),
        })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--fast", type=int, default=12)
    ap.add_argument("--slow", type=int, default=48)
    ap.add_argument("--symbols", default=None,
                    help="콤마 구분. 미지정 시 18종 default")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    symbols = ([s.strip() for s in args.symbols.split(",")]
               if args.symbols else ALT18)

    # 4h × 365d = ~2190 bars per year
    bars_per_day = {"1h": 24, "4h": 6, "1d": 1}.get(args.timeframe, 6)
    cfg = BacktestConfig(
        fast_ma=args.fast, slow_ma=args.slow,
        bars_per_year=bars_per_day * 365,
        cost_bps_per_side=4.0,
    )

    print(f"=== TP Sweep — {args.timeframe} fast={args.fast} slow={args.slow}, "
          f"{args.days}d, {len(symbols)} symbols ===")
    print(f"Rules: {len(build_rules())} (baseline + tiered×5 + partial×4 + hard×4)")

    per_symbol: dict[str, dict[str, dict]] = {}
    for i, sym in enumerate(symbols, 1):
        print(f"  [{i:2d}/{len(symbols)}] {sym} …", end="", flush=True)
        df = load_symbol(sym, args.days, args.timeframe)
        if df is None or len(df) < args.slow + 30:
            print(" SKIP (no data)")
            continue
        per_symbol[sym] = run_for_symbol(df, args.fast, args.slow, cfg)
        print(f" {len(df)} bars OK")

    if not per_symbol:
        print("NO DATA — abort")
        return

    df_agg = aggregate(per_symbol).sort_values("mean_sharpe", ascending=False)

    print("\n=== 룰별 18종 평균 (Sharpe 내림차순) ===")
    pd.set_option("display.float_format", lambda v: f"{v:>8.3f}")
    print(df_agg.to_string(index=False))

    baseline = df_agg[df_agg["rule"] == "baseline_trail2"].iloc[0]
    print(f"\n=== baseline 대비 (Sharpe 차이) ===")
    df_agg["sharpe_delta_vs_baseline"] = df_agg["mean_sharpe"] - baseline["mean_sharpe"]
    df_agg["return_delta_vs_baseline"] = df_agg["mean_return%"] - baseline["mean_return%"]
    df_agg["dd_delta_vs_baseline"] = df_agg["mean_maxdd%"] - baseline["mean_maxdd%"]
    print(df_agg[["rule", "mean_sharpe", "sharpe_delta_vs_baseline",
                   "return_delta_vs_baseline", "dd_delta_vs_baseline"]]
          .to_string(index=False))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.out) if args.out else (
        ROOT / "data" / f"backtest_tp_sweep_{args.days}d_{ts}.json")
    payload = {
        "args": vars(args), "rules": [r[0] for r in build_rules()],
        "per_symbol": {k: {r: dict(v) for r, v in s.items()}
                        for k, s in per_symbol.items()},
        "aggregate": df_agg.to_dict(orient="records"),
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\n[saved] {out_path}")


if __name__ == "__main__":
    main()
