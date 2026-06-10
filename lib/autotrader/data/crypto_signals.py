"""Crypto microstructure signals (funding rate, L/S ratio, OI) — backtest 인프라.

Binance Futures API 의 무료 historical 데이터:
  - funding rate     : 8시간 단위, 1000회 조회 가능 (~333일)
  - L/S account ratio: 1시간 단위, 500회 조회 (~21일)
  - L/S position ratio: 동일
  - Open Interest    : 1시간 단위, 500회 조회

캐시: data/crypto_signals/<symbol>_<signal>.parquet
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[5] / "data" / "crypto_signals"


def _client():
    """Binance Futures MAINNET public client (read-only).

    Funding rate / L/S ratio / OI 같은 시장 데이터는 testnet 에 없거나 비어있음.
    public endpoint 는 auth 없이도 호출 가능. read-only 라 안전.
    """
    from binance.client import Client
    return Client("", "")


@dataclass
class SignalCache:
    cache_dir: Path = DEFAULT_CACHE_DIR

    def __post_init__(self):
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str, name: str) -> Path:
        sym_safe = symbol.replace("/", "").replace(":", "_")
        return self.cache_dir / f"{sym_safe}_{name}.parquet"

    def load(self, symbol: str, name: str) -> pd.DataFrame | None:
        p = self._path(symbol, name)
        return pd.read_parquet(p) if p.exists() else None

    def save(self, symbol: str, name: str, df: pd.DataFrame):
        if df is None or df.empty:
            return
        df.to_parquet(self._path(symbol, name))


def fetch_funding_rate(symbol: str = "BTCUSDT", days: int = 60,
                        use_cache: bool = True,
                        cache: SignalCache | None = None) -> pd.DataFrame:
    """Funding rate history. 8시간 단위 (하루 3회).

    Returns DataFrame columns: funding_rate, mark_price (Index: tz-aware UTC)
    """
    cache = cache or SignalCache()
    if use_cache:
        cached = cache.load(symbol, "funding")
        if cached is not None and not cached.empty:
            cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
            if cached.index.min() <= cutoff:
                return cached.loc[cached.index >= cutoff]

    cl = _client()
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000

    rows = []
    cur = start_ms
    while cur < end_ms:
        try:
            chunk = cl.futures_funding_rate(
                symbol=symbol, startTime=cur, limit=1000
            )
        except Exception as e:
            print(f"[funding] fetch fail at {cur}: {e}")
            break
        if not chunk:
            break
        rows.extend(chunk)
        last_t = int(chunk[-1]["fundingTime"])
        if last_t <= cur:
            break
        cur = last_t + 1
        time.sleep(0.2)
        if len(chunk) < 1000:
            break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["funding_rate"] = df["fundingRate"].astype(float)
    df["mark_price"] = df.get("markPrice", 0).astype(float)
    df = df.set_index("ts").sort_index()
    df = df[["funding_rate", "mark_price"]]
    df = df[~df.index.duplicated(keep="first")]
    cache.save(symbol, "funding", df)
    return df


def fetch_long_short_ratio(symbol: str = "BTCUSDT",
                             period: str = "1h",
                             days: int = 30,
                             use_cache: bool = True,
                             cache: SignalCache | None = None) -> pd.DataFrame:
    """Top trader long/short account ratio. period: 5m,15m,30m,1h,2h,4h,6h,12h,1d.

    1h period = 500 bars max = ~21 days history.

    Returns DataFrame columns: ls_account_ratio, long_pct, short_pct
    """
    cache = cache or SignalCache()
    cache_name = f"lsratio_{period}"
    if use_cache:
        cached = cache.load(symbol, cache_name)
        if cached is not None and not cached.empty:
            cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
            if cached.index.min() <= cutoff:
                return cached.loc[cached.index >= cutoff]

    cl = _client()
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    rows = []
    cur = start_ms
    while cur < end_ms:
        try:
            chunk = cl.futures_top_longshort_account_ratio(
                symbol=symbol, period=period, startTime=cur, limit=500
            )
        except Exception as e:
            print(f"[ls_ratio] fetch fail: {e}")
            break
        if not chunk:
            break
        rows.extend(chunk)
        last_t = int(chunk[-1]["timestamp"])
        if last_t <= cur:
            break
        cur = last_t + 1
        time.sleep(0.2)
        if len(chunk) < 500:
            break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["ls_account_ratio"] = df["longShortRatio"].astype(float)
    df["long_pct"] = df["longAccount"].astype(float)
    df["short_pct"] = df["shortAccount"].astype(float)
    df = df.set_index("ts").sort_index()
    df = df[["ls_account_ratio", "long_pct", "short_pct"]]
    df = df[~df.index.duplicated(keep="first")]
    cache.save(symbol, cache_name, df)
    return df


def fetch_open_interest(symbol: str = "BTCUSDT",
                         period: str = "1h",
                         days: int = 30,
                         use_cache: bool = True,
                         cache: SignalCache | None = None) -> pd.DataFrame:
    """Open Interest history. period: 5m,15m,30m,1h,2h,4h,6h,12h,1d.

    Returns DataFrame columns: oi_amount (contracts), oi_value (USDT)
    """
    cache = cache or SignalCache()
    cache_name = f"oi_{period}"
    if use_cache:
        cached = cache.load(symbol, cache_name)
        if cached is not None and not cached.empty:
            cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
            if cached.index.min() <= cutoff:
                return cached.loc[cached.index >= cutoff]

    cl = _client()
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    rows = []
    cur = start_ms
    while cur < end_ms:
        try:
            chunk = cl.futures_open_interest_hist(
                symbol=symbol, period=period, startTime=cur, limit=500
            )
        except Exception as e:
            print(f"[oi] fetch fail: {e}")
            break
        if not chunk:
            break
        rows.extend(chunk)
        last_t = int(chunk[-1]["timestamp"])
        if last_t <= cur:
            break
        cur = last_t + 1
        time.sleep(0.2)
        if len(chunk) < 500:
            break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["oi_amount"] = df["sumOpenInterest"].astype(float)
    df["oi_value"] = df["sumOpenInterestValue"].astype(float)
    df = df.set_index("ts").sort_index()
    df = df[["oi_amount", "oi_value"]]
    df = df[~df.index.duplicated(keep="first")]
    cache.save(symbol, cache_name, df)
    return df


__all__ = [
    "fetch_funding_rate",
    "fetch_long_short_ratio",
    "fetch_open_interest",
    "SignalCache",
]
