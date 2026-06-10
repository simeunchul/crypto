"""Crypto OHLCV historical bars via CCXT — backtest 인프라.

CCXT 가 Binance Futures 의 historical kline 을 무료로 제공.
- 1m: 최근 ~1500 bar (~1일)
- 5m: 최근 ~1500 bar (~5일)
- 1h: 최근 ~1500 bar (~62일)
- 1d: 수년치

다중 호출 (since 파라미터 walking) 으로 임의 길이 backfill 가능.
캐시: data/crypto_bars/<symbol>_<timeframe>.parquet
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "crypto_bars"

# CCXT timeframe → ms
_TF_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def _exchange(market: str = "futures"):
    """CCXT Binance USDT-M futures or spot exchange."""
    import ccxt
    if market == "futures":
        return ccxt.binanceusdm()  # USDT-M perpetual
    return ccxt.binance()


@dataclass
class CryptoBarsCache:
    cache_dir: Path = DEFAULT_CACHE_DIR
    timeframe: str = "1h"
    market: str = "futures"

    def __post_init__(self):
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str) -> Path:
        sym_safe = symbol.replace("/", "").replace(":", "_")
        return self.cache_dir / f"{sym_safe}_{self.market}_{self.timeframe}.parquet"

    def load(self, symbol: str) -> pd.DataFrame | None:
        p = self._path(symbol)
        return pd.read_parquet(p) if p.exists() else None

    def save(self, symbol: str, df: pd.DataFrame):
        if df is None or df.empty:
            return
        df.to_parquet(self._path(symbol))


def fetch_crypto_bars(
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    days: int = 30,
    market: str = "futures",
    use_cache: bool = True,
    cache: CryptoBarsCache | None = None,
) -> pd.DataFrame:
    """Walked-fetch + cache.

    Args:
        symbol: 'BTC/USDT', 'ETH/USDT' 등 CCXT 표준
        timeframe: '1m'|'5m'|'15m'|'1h'|'4h'|'1d'
        days: 몇 일치 백필
        market: 'futures' (USDT-M perp) | 'spot'

    Returns:
        DataFrame with columns: open, high, low, close, volume.
        Index: tz-aware DatetimeIndex (UTC).
    """
    cache = cache or CryptoBarsCache(timeframe=timeframe, market=market)
    if use_cache:
        cached = cache.load(symbol)
        if cached is not None and not cached.empty:
            cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
            if cached.index.min() <= cutoff:
                return cached.loc[cached.index >= cutoff]

    ex = _exchange(market)
    tf_ms = _TF_MS[timeframe]
    end = ex.milliseconds()
    start = end - days * 86_400_000
    limit_per_call = 1000   # binanceusdm 호출당 max 1000

    all_bars: list[list] = []
    since = start
    last_ts_seen = -1
    while since < end:
        try:
            chunk = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=limit_per_call)
        except Exception as e:
            print(f"[crypto_bars] fetch fail at since={since}: {e}")
            break
        if not chunk:
            break
        all_bars.extend(chunk)
        last_ts = chunk[-1][0]
        # Stuck detection (서버가 빈 응답 또는 동일 ts 반복)
        if last_ts <= last_ts_seen:
            break
        last_ts_seen = last_ts
        since = last_ts + tf_ms
        time.sleep(getattr(ex, "rateLimit", 100) / 1000.0)

    if not all_bars:
        return pd.DataFrame()

    df = pd.DataFrame(all_bars, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates(subset="ts").sort_values("ts").reset_index(drop=True)
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.index.name = "datetime"
    df = df.drop(columns=["ts"])

    cache.save(symbol, df)
    return df


def returns_summary(df: pd.DataFrame) -> dict:
    """Quick descriptive stats."""
    if df.empty:
        return {}
    rets = df["close"].pct_change().dropna()
    return {
        "n_bars": len(df),
        "start": df.index.min().isoformat(),
        "end": df.index.max().isoformat(),
        "mean_return": float(rets.mean()),
        "std_return": float(rets.std()),
        "ann_vol_pct": float(rets.std() * (24 * 365) ** 0.5 * 100),  # 1h bar 기준
        "sharpe": float(rets.mean() / rets.std() * (24 * 365) ** 0.5) if rets.std() > 0 else float("nan"),
        "min_return": float(rets.min()),
        "max_return": float(rets.max()),
        "max_drawdown_pct": float(((df["close"] / df["close"].cummax()) - 1).min() * 100),
    }


__all__ = [
    "fetch_crypto_bars",
    "CryptoBarsCache",
    "returns_summary",
    "DEFAULT_CACHE_DIR",
]
