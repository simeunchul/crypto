"""Crypto momentum (MA crossover) backtest.

Long-only / long-short 양방향 가능 (선물). 가장 단순한 trend following.

Logic:
  fast_ma > slow_ma → long
  fast_ma < slow_ma → short (또는 flat for long-only)

거래비용: Binance Futures 기준 taker 0.04% per side (round-trip 8bps).
가짜 USDT testnet 환경이지만 실거래도 같은 fee 구조.
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class MomentumConfig:
    fast_ma: int = 12             # 12h
    slow_ma: int = 48             # 48h = 2 days
    long_short: bool = True       # True = both, False = long-only
    cost_bps_per_side: float = 4.0  # Binance Futures taker
    initial_capital: float = 10_000.0


def run_ma_crossover(df: pd.DataFrame, cfg: MomentumConfig) -> pd.DataFrame:
    """1-bar lag 적용 (look-ahead 회피).

    Returns DataFrame with columns:
      close, fast_ma, slow_ma, signal, position, ret, cost, net_ret, equity
    """
    if df.empty or len(df) < cfg.slow_ma + 2:
        return pd.DataFrame()

    out = df[["close"]].copy()
    out["fast_ma"] = out["close"].rolling(cfg.fast_ma).mean()
    out["slow_ma"] = out["close"].rolling(cfg.slow_ma).mean()

    # signal: +1 (long), -1 (short), 0 (flat)
    sig = np.where(out["fast_ma"] > out["slow_ma"], 1.0, -1.0 if cfg.long_short else 0.0)
    # 1-bar lag (현재 bar 의 MA 보고 다음 bar 시점에 진입)
    out["signal"] = pd.Series(sig, index=out.index).shift(1).fillna(0)
    out["position"] = out["signal"]

    # bar 수익률
    out["ret"] = out["close"].pct_change().fillna(0)
    # 포지션 × 수익률
    out["pos_ret"] = out["position"] * out["ret"]

    # 거래비용 — 포지션 변경 시 발생
    out["pos_change"] = out["position"].diff().abs().fillna(0)
    cost_per_round = cfg.cost_bps_per_side / 10_000.0
    # |Δposition| 이 1 = full flip = 양쪽 (close + open) 발생 → 2 × cost_per_side
    # 동일 방향 사이즈 변경은 1 × cost_per_side (here 단순화: 비례)
    out["cost"] = out["pos_change"] * cost_per_round
    out["net_ret"] = out["pos_ret"] - out["cost"]

    # 자본 곡선
    out["equity"] = (1 + out["net_ret"]).cumprod() * cfg.initial_capital

    return out


def summarize(out: pd.DataFrame, bars_per_year: int = 8760) -> dict:
    """Backtest 요약 통계.

    bars_per_year 기본 = 8760 (1h × 24 × 365). crypto 는 24/7 이라 252일×6.5h 안 함.
    """
    if out.empty:
        return {}

    n = len(out)
    rets = out["net_ret"]
    equity = out["equity"]

    final_equity = float(equity.iloc[-1])
    total_return = (final_equity / equity.iloc[0]) - 1
    duration_years = n / bars_per_year
    cagr = (final_equity / equity.iloc[0]) ** (1 / duration_years) - 1 if duration_years > 0 else 0

    sharpe = float(rets.mean() / rets.std() * np.sqrt(bars_per_year)) if rets.std() > 0 else float("nan")
    max_dd = float(((equity / equity.cummax()) - 1).min())

    n_long = int((out["position"] == 1).sum())
    n_short = int((out["position"] == -1).sum())
    n_flat = int((out["position"] == 0).sum())
    n_flips = int((out["position"].diff().abs() > 0).sum())

    win_rate = float((rets > 0).mean())

    return {
        "n_bars": n,
        "duration_days": float(n / 24),
        "final_equity": final_equity,
        "total_return_pct": float(total_return * 100),
        "cagr_pct": float(cagr * 100),
        "sharpe_annualized": sharpe,
        "max_drawdown_pct": float(max_dd * 100),
        "win_rate": win_rate,
        "n_long_bars": n_long,
        "n_short_bars": n_short,
        "n_flat_bars": n_flat,
        "n_position_flips": n_flips,
        "total_cost_pct": float(out["cost"].sum() * 100),
        "best_bar": float(rets.max() * 100),
        "worst_bar": float(rets.min() * 100),
    }


def baseline_buyhold(df: pd.DataFrame, cfg: MomentumConfig) -> dict:
    """Buy-and-hold baseline (수수료 1회만, 시작 시 매수)."""
    if df.empty:
        return {}
    cost = cfg.cost_bps_per_side / 10_000.0
    rets = df["close"].pct_change().fillna(0)
    equity = (1 + rets).cumprod() * cfg.initial_capital * (1 - cost)
    final = float(equity.iloc[-1])
    return {
        "final_equity": final,
        "total_return_pct": float((final / cfg.initial_capital - 1) * 100),
        "max_drawdown_pct": float(((equity / equity.cummax()) - 1).min() * 100),
        "sharpe_annualized": float(rets.mean() / rets.std() * np.sqrt(8760)) if rets.std() > 0 else float("nan"),
    }


__all__ = [
    "MomentumConfig",
    "run_ma_crossover",
    "summarize",
    "baseline_buyhold",
]
