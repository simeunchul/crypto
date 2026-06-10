"""Crypto multi-strategy backtest — 8가지 룰 비교.

각 strategy 는 같은 데이터프레임 (close, funding, ls_ratio, oi 등) 받아
position 시계열 (-1, 0, +1) 출력.
공통 backtest engine 으로 P&L / Sharpe / DD 통일 측정.
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class BacktestConfig:
    fast_ma: int = 6
    slow_ma: int = 24
    cost_bps_per_side: float = 4.0   # Binance Futures taker
    initial_capital: float = 10_000.0
    bars_per_year: int = 8760        # 1h × 24 × 365
    # Filter thresholds
    funding_overcrowd: float = 0.0005    # 0.05% per 8h = LONG 과밀 기준
    funding_undercrowd: float = -0.0005  # SHORT 과밀
    ls_overcrowd_long: float = 0.70      # 70% 이상 LONG = 과밀
    ls_overcrowd_short: float = 0.30     # 30% 미만 LONG = SHORT 과밀
    bb_window: int = 20
    bb_std: float = 2.0


# ──────────────────────────────────────────────────────────── individual strategies

def strat_buyhold(df: pd.DataFrame, cfg: BacktestConfig) -> pd.Series:
    """A: Buy and hold (baseline)."""
    return pd.Series(1.0, index=df.index)


def strat_trend(df: pd.DataFrame, cfg: BacktestConfig) -> pd.Series:
    """B: Pure trend MA crossover (long-short)."""
    fast = df["close"].rolling(cfg.fast_ma).mean()
    slow = df["close"].rolling(cfg.slow_ma).mean()
    sig = np.where(fast > slow, 1.0, -1.0)
    return pd.Series(sig, index=df.index).shift(1).fillna(0)


def strat_trend_long_only(df: pd.DataFrame, cfg: BacktestConfig) -> pd.Series:
    """C: Pure trend long-only (현재 운영 중 룰)."""
    fast = df["close"].rolling(cfg.fast_ma).mean()
    slow = df["close"].rolling(cfg.slow_ma).mean()
    sig = np.where(fast > slow, 1.0, 0.0)
    return pd.Series(sig, index=df.index).shift(1).fillna(0)


def strat_trend_funding_filter(df: pd.DataFrame, cfg: BacktestConfig) -> pd.Series:
    """D: Trend + funding rate filter — 과밀 진입 회피.

    LONG 진입 조건: trend up AND funding < overcrowd 임계
    SHORT 진입 조건: trend down AND funding > undercrowd 임계
    """
    if "funding_rate" not in df.columns:
        return strat_trend(df, cfg)
    fast = df["close"].rolling(cfg.fast_ma).mean()
    slow = df["close"].rolling(cfg.slow_ma).mean()
    funding = df["funding_rate"].fillna(0)
    long_cond = (fast > slow) & (funding < cfg.funding_overcrowd)
    short_cond = (fast < slow) & (funding > cfg.funding_undercrowd)
    sig = np.where(long_cond, 1.0, np.where(short_cond, -1.0, 0.0))
    return pd.Series(sig, index=df.index).shift(1).fillna(0)


def strat_trend_ls_filter(df: pd.DataFrame, cfg: BacktestConfig) -> pd.Series:
    """E: Trend + L/S ratio filter."""
    if "long_pct" not in df.columns:
        return strat_trend(df, cfg)
    fast = df["close"].rolling(cfg.fast_ma).mean()
    slow = df["close"].rolling(cfg.slow_ma).mean()
    long_pct = df["long_pct"].fillna(0.5)
    long_cond = (fast > slow) & (long_pct < cfg.ls_overcrowd_long)
    short_cond = (fast < slow) & (long_pct > cfg.ls_overcrowd_short)
    sig = np.where(long_cond, 1.0, np.where(short_cond, -1.0, 0.0))
    return pd.Series(sig, index=df.index).shift(1).fillna(0)


def strat_trend_3factor(df: pd.DataFrame, cfg: BacktestConfig) -> pd.Series:
    """F: Trend + funding + L/S — 3-factor combined.

    가장 conservative: 모든 조건 일치할 때만 진입.
    """
    if "funding_rate" not in df.columns or "long_pct" not in df.columns:
        return strat_trend(df, cfg)
    fast = df["close"].rolling(cfg.fast_ma).mean()
    slow = df["close"].rolling(cfg.slow_ma).mean()
    funding = df["funding_rate"].fillna(0)
    long_pct = df["long_pct"].fillna(0.5)
    long_cond = (
        (fast > slow)
        & (funding < cfg.funding_overcrowd)
        & (long_pct < cfg.ls_overcrowd_long)
    )
    short_cond = (
        (fast < slow)
        & (funding > cfg.funding_undercrowd)
        & (long_pct > cfg.ls_overcrowd_short)
    )
    sig = np.where(long_cond, 1.0, np.where(short_cond, -1.0, 0.0))
    return pd.Series(sig, index=df.index).shift(1).fillna(0)


def strat_bollinger_mr(df: pd.DataFrame, cfg: BacktestConfig) -> pd.Series:
    """G: Bollinger Band mean reversion.

    price > upper → SHORT (반전 베팅)
    price < lower → LONG (반전 베팅)
    중심선 회귀 시 청산
    """
    ma = df["close"].rolling(cfg.bb_window).mean()
    sd = df["close"].rolling(cfg.bb_window).std()
    upper = ma + cfg.bb_std * sd
    lower = ma - cfg.bb_std * sd
    pos = pd.Series(0.0, index=df.index)
    state = 0
    for i in range(len(df)):
        if pd.isna(upper.iloc[i]):
            pos.iloc[i] = state
            continue
        p = df["close"].iloc[i]
        m = ma.iloc[i]
        if state == 0:
            if p > upper.iloc[i]:
                state = -1
            elif p < lower.iloc[i]:
                state = 1
        elif state == 1 and p >= m:
            state = 0
        elif state == -1 and p <= m:
            state = 0
        pos.iloc[i] = state
    return pos.shift(1).fillna(0)


def strat_funding_contrarian(df: pd.DataFrame, cfg: BacktestConfig) -> pd.Series:
    """H: Pure funding rate contrarian — 펀딩 자체로 매매.

    funding > +overcrowd → SHORT (LONG 과밀, 반전 베팅)
    funding < -overcrowd → LONG (SHORT 과밀, 반전 베팅)
    중간: FLAT
    """
    if "funding_rate" not in df.columns:
        return pd.Series(0.0, index=df.index)
    funding = df["funding_rate"].fillna(0)
    sig = np.where(funding > cfg.funding_overcrowd, -1.0,
                    np.where(funding < cfg.funding_undercrowd, 1.0, 0.0))
    return pd.Series(sig, index=df.index).shift(1).fillna(0)


# ──────────────────────────────────────────────────────────── backtest engine

def backtest(df: pd.DataFrame, position: pd.Series,
              cfg: BacktestConfig | None = None) -> pd.DataFrame:
    """공통 backtest. 1-bar lag 가정 (shift 는 strategy 함수에서 이미 적용)."""
    cfg = cfg or BacktestConfig()
    out = df[["close"]].copy()
    out["position"] = position.fillna(0)
    out["ret"] = out["close"].pct_change().fillna(0)
    out["pos_ret"] = out["position"] * out["ret"]
    out["pos_change"] = out["position"].diff().abs().fillna(0)
    cost_per_round = cfg.cost_bps_per_side / 10_000.0
    out["cost"] = out["pos_change"] * cost_per_round
    out["net_ret"] = out["pos_ret"] - out["cost"]
    out["equity"] = (1 + out["net_ret"]).cumprod() * cfg.initial_capital
    return out


def summarize(out: pd.DataFrame, cfg: BacktestConfig | None = None) -> dict:
    cfg = cfg or BacktestConfig()
    if out.empty:
        return {}
    rets = out["net_ret"]
    equity = out["equity"]
    n = len(out)
    final = float(equity.iloc[-1])
    total_return = (final / equity.iloc[0]) - 1
    sharpe = float(rets.mean() / rets.std() * np.sqrt(cfg.bars_per_year)) if rets.std() > 0 else float("nan")
    max_dd = float(((equity / equity.cummax()) - 1).min())
    n_long = int((out["position"] == 1).sum())
    n_short = int((out["position"] == -1).sum())
    n_flat = int((out["position"] == 0).sum())
    n_flips = int((out["position"].diff().abs() > 0).sum())
    return {
        "n_bars": n,
        "duration_days": float(n / 24),
        "final_equity": final,
        "total_return_pct": float(total_return * 100),
        "sharpe": sharpe,
        "max_drawdown_pct": float(max_dd * 100),
        "n_long_bars": n_long,
        "n_short_bars": n_short,
        "n_flat_bars": n_flat,
        "n_position_flips": n_flips,
        "total_cost_pct": float(out["cost"].sum() * 100),
        "win_rate": float((rets > 0).mean()),
    }


# ──────────────────────────────────────────────────────────── data merge

def merge_signals(price_df: pd.DataFrame,
                    funding_df: pd.DataFrame | None = None,
                    ls_df: pd.DataFrame | None = None,
                    oi_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """price + signals 시계열 결합. funding 은 8h 단위라 forward-fill.

    Args:
      price_df: index=datetime, col 'close' (and optionally open/high/low/volume)
      funding_df: index=datetime, col 'funding_rate'
      ls_df: index=datetime, cols 'long_pct', 'short_pct', 'ls_account_ratio'
      oi_df: index=datetime, col 'oi_value'
    """
    out = price_df.copy()
    if funding_df is not None and not funding_df.empty:
        out = out.join(funding_df["funding_rate"], how="left")
        out["funding_rate"] = out["funding_rate"].ffill().fillna(0)
    if ls_df is not None and not ls_df.empty:
        out = out.join(ls_df[["long_pct", "short_pct", "ls_account_ratio"]], how="left")
        for c in ["long_pct", "short_pct", "ls_account_ratio"]:
            if c in out.columns:
                out[c] = out[c].ffill()
    if oi_df is not None and not oi_df.empty:
        out = out.join(oi_df["oi_value"], how="left")
        out["oi_value"] = out["oi_value"].ffill()
    return out


# Strategy registry for sweep
STRATEGIES = {
    "A_buyhold":          strat_buyhold,
    "B_trend":            strat_trend,
    "C_trend_long_only":  strat_trend_long_only,
    "D_trend+funding":    strat_trend_funding_filter,
    "E_trend+lsratio":    strat_trend_ls_filter,
    "F_trend+3factor":    strat_trend_3factor,
    "G_bollinger_mr":     strat_bollinger_mr,
    "H_funding_contra":   strat_funding_contrarian,
}


# ──────────────────────────────────────────────────────── Stop-loss / Take-profit overlay

def apply_risk_overlay(
    df: pd.DataFrame,
    base_position: pd.Series,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    trailing_stop_pct: float | None = None,
) -> pd.Series:
    """Path-dependent overlay applying stop-loss / take-profit / trailing stop.

    Args:
        base_position: signal series (-1/0/+1) from base strategy
        stop_loss_pct: e.g., 0.03 = exit LONG at -3% from entry, exit SHORT at +3%
        take_profit_pct: e.g., 0.05 = exit LONG at +5% from entry, exit SHORT at -5%
        trailing_stop_pct: e.g., 0.02 = exit LONG when price drops 2% from peak after entry

    Logic:
      - When in position (LONG or SHORT), check stops first
      - If stopped out, set position to 0 until base signal changes
      - Re-enter only when base signal flips to non-zero (avoid immediate re-entry at same bar)
    """
    out = pd.Series(0, index=base_position.index, dtype=float)
    state = 0
    entry_price = None
    high_water = None   # for trailing stop on LONG (track high)
    low_water = None    # for trailing stop on SHORT (track low)
    stopped_until_signal_change = 0  # remember last signal we stopped on

    for i in range(len(df)):
        bp = int(round(float(base_position.iloc[i])))
        p = float(df["close"].iloc[i])

        # 1. Stop check (only if we're holding)
        if state != 0 and entry_price is not None:
            stopped = False
            if state == 1:   # LONG
                if stop_loss_pct and p < entry_price * (1 - stop_loss_pct):
                    stopped = True
                elif take_profit_pct and p > entry_price * (1 + take_profit_pct):
                    stopped = True
                elif trailing_stop_pct:
                    high_water = max(high_water or p, p)
                    if p < high_water * (1 - trailing_stop_pct):
                        stopped = True
            elif state == -1:  # SHORT
                if stop_loss_pct and p > entry_price * (1 + stop_loss_pct):
                    stopped = True
                elif take_profit_pct and p < entry_price * (1 - take_profit_pct):
                    stopped = True
                elif trailing_stop_pct:
                    low_water = min(low_water or p, p)
                    if p > low_water * (1 + trailing_stop_pct):
                        stopped = True

            if stopped:
                stopped_until_signal_change = state
                state = 0
                entry_price = None
                high_water = None
                low_water = None

        # 2. Signal-based logic
        if state == 0:
            # FLAT: enter only if base signal is non-zero AND different from last stopped signal
            if bp != 0 and bp != stopped_until_signal_change:
                state = bp
                entry_price = p
                high_water = p if bp == 1 else None
                low_water = p if bp == -1 else None
                stopped_until_signal_change = 0  # reset
            elif bp != stopped_until_signal_change:
                # Base signal changed but to FLAT; reset stop-block
                stopped_until_signal_change = 0
        else:
            # In position: signal change → flip
            if bp != state:
                if bp == 0:
                    # Signal went flat → close
                    state = 0
                    entry_price = None
                    high_water = None
                    low_water = None
                else:
                    # Signal flipped → close + open opposite
                    state = bp
                    entry_price = p
                    high_water = p if bp == 1 else None
                    low_water = p if bp == -1 else None

        out.iloc[i] = state

    return out


def make_strategy_with_overlay(base_strategy_fn, **overlay_kwargs):
    """Wrap a base strategy with a risk overlay, returning a new strategy function."""
    def wrapped(df, cfg):
        base_pos = base_strategy_fn(df, cfg)
        return apply_risk_overlay(df, base_pos, **overlay_kwargs)
    return wrapped


# ──────────────────────────────────────────────────────── Adaptive trailing stops

def compute_atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average True Range — Wilder 1978.

    True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    ATR = N-period EMA of TR (or simple mean)
    """
    high = df["high"] if "high" in df.columns else df["close"]
    low = df["low"] if "low" in df.columns else df["close"]
    close_prev = df["close"].shift(1)
    tr1 = high - low
    tr2 = (high - close_prev).abs()
    tr3 = (low - close_prev).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window).mean()


def apply_atr_trailing(
    df: pd.DataFrame,
    base_position: pd.Series,
    atr_window: int = 14,
    atr_multiple: float = 3.0,
) -> pd.Series:
    """Trailing stop where distance = ATR × multiple. Variance-adaptive.

    LONG: high_water - ATR × multiple = stop
    SHORT: low_water + ATR × multiple = stop
    """
    atr = compute_atr(df, atr_window).fillna(0)
    out = pd.Series(0, index=base_position.index, dtype=float)
    state = 0
    entry_price = None
    high_water = None
    low_water = None
    stopped_until_signal_change = 0

    for i in range(len(df)):
        bp = int(round(float(base_position.iloc[i])))
        p = float(df["close"].iloc[i])
        a = float(atr.iloc[i])

        if state != 0 and entry_price is not None and a > 0:
            stopped = False
            if state == 1:
                high_water = max(high_water or p, p)
                if p < high_water - atr_multiple * a:
                    stopped = True
            elif state == -1:
                low_water = min(low_water or p, p)
                if p > low_water + atr_multiple * a:
                    stopped = True

            if stopped:
                stopped_until_signal_change = state
                state = 0
                entry_price = None
                high_water = None
                low_water = None

        if state == 0:
            if bp != 0 and bp != stopped_until_signal_change:
                state = bp
                entry_price = p
                high_water = p if bp == 1 else None
                low_water = p if bp == -1 else None
                stopped_until_signal_change = 0
            elif bp != stopped_until_signal_change:
                stopped_until_signal_change = 0
        else:
            if bp != state:
                if bp == 0:
                    state = 0
                    entry_price = None
                    high_water = None
                    low_water = None
                else:
                    state = bp
                    entry_price = p
                    high_water = p if bp == 1 else None
                    low_water = p if bp == -1 else None

        out.iloc[i] = state

    return out


def apply_trailing_only(
    df: pd.DataFrame,
    base_position: pd.Series,
    trailing_stop_pct: float = 0.02,
) -> pd.Series:
    """Pure trailing-only exit: signal entry only, signal changes IGNORED while holding.

    사용자 제안: trend 반전 신호로 청산 X. 오직 trailing stop 도달 시만 청산.
    """
    out = pd.Series(0, index=base_position.index, dtype=float)
    state = 0
    entry_price = None
    high_water = None
    low_water = None

    for i in range(len(df)):
        bp = int(round(float(base_position.iloc[i])))
        p = float(df["close"].iloc[i])

        # 1. Trailing stop check (only this can exit)
        if state != 0 and entry_price is not None:
            stopped = False
            if state == 1:
                high_water = max(high_water or p, p)
                if p < high_water * (1 - trailing_stop_pct):
                    stopped = True
            elif state == -1:
                low_water = min(low_water or p, p)
                if p > low_water * (1 + trailing_stop_pct):
                    stopped = True

            if stopped:
                state = 0
                entry_price = None
                high_water = None
                low_water = None

        # 2. Entry only when flat (signal change while holding = IGNORED)
        if state == 0 and bp != 0:
            state = bp
            entry_price = p
            high_water = p if bp == 1 else None
            low_water = p if bp == -1 else None

        out.iloc[i] = state

    return out


def apply_trend_adaptive_trailing(
    df: pd.DataFrame,
    base_position: pd.Series,
    fast_ma: int = 6,
    slow_ma: int = 24,
    weak_threshold: float = 0.01,    # |fast-slow|/slow < 1% = weak
    strong_threshold: float = 0.03,  # > 3% = strong
    weak_trail: float = 0.02,        # -2% for weak
    medium_trail: float = 0.03,      # -3% for medium
    strong_trail: float = 0.05,      # -5% for strong
) -> pd.Series:
    """Trend-strength-adaptive trailing.

    강한 trend → 더 넓은 trail (-5%)
    약한 trend → 더 좁은 trail (-2%)
    """
    fast = df["close"].rolling(fast_ma).mean()
    slow = df["close"].rolling(slow_ma).mean()
    trend_strength = (fast - slow).abs() / slow.replace(0, pd.NA)
    trend_strength = trend_strength.fillna(0)

    # Per-bar trail % (lookup based on strength)
    trail_pct_series = pd.Series(weak_trail, index=df.index)
    trail_pct_series[trend_strength >= weak_threshold] = medium_trail
    trail_pct_series[trend_strength >= strong_threshold] = strong_trail

    out = pd.Series(0, index=base_position.index, dtype=float)
    state = 0
    entry_price = None
    high_water = None
    low_water = None
    stopped_until_signal_change = 0

    for i in range(len(df)):
        bp = int(round(float(base_position.iloc[i])))
        p = float(df["close"].iloc[i])
        trail = float(trail_pct_series.iloc[i])

        if state != 0 and entry_price is not None:
            stopped = False
            if state == 1:
                high_water = max(high_water or p, p)
                if p < high_water * (1 - trail):
                    stopped = True
            elif state == -1:
                low_water = min(low_water or p, p)
                if p > low_water * (1 + trail):
                    stopped = True

            if stopped:
                stopped_until_signal_change = state
                state = 0
                entry_price = None
                high_water = None
                low_water = None

        if state == 0:
            if bp != 0 and bp != stopped_until_signal_change:
                state = bp
                entry_price = p
                high_water = p if bp == 1 else None
                low_water = p if bp == -1 else None
                stopped_until_signal_change = 0
            elif bp != stopped_until_signal_change:
                stopped_until_signal_change = 0
        else:
            if bp != state:
                if bp == 0:
                    state = 0
                    entry_price = None
                    high_water = None
                    low_water = None
                else:
                    state = bp
                    entry_price = p
                    high_water = p if bp == 1 else None
                    low_water = p if bp == -1 else None

        out.iloc[i] = state

    return out


__all__ = [
    "BacktestConfig",
    "STRATEGIES",
    "backtest",
    "summarize",
    "merge_signals",
    "apply_risk_overlay",
    "make_strategy_with_overlay",
    "compute_atr",
    "apply_atr_trailing",
    "apply_trend_adaptive_trailing",
    "apply_trailing_only",
]
