"""Binance Futures Testnet client wrapper.

Testnet 은 가짜 USDT 환경 — 실돈 X. 진짜 시세 + 진짜 매매 흐름을 학습 가능.
KIS client 와 비슷한 인터페이스 (quote / balance / order) 로 wrapping 하여
기존 strategy 모듈이 거의 그대로 재사용 가능.

Endpoints:
  Spot Testnet     : https://testnet.binance.vision  (BTC/ETH 등 제한)
  Futures Testnet  : https://testnet.binancefuture.com  (USDT-M Perp 전부)

발급: testnet.binancefuture.com → API Management. 5분 안 발급, 가짜 USDT 만 거래.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BinanceTestnetConfig:
    api_key: str
    api_secret: str
    market: str = "futures"        # "futures" | "spot"
    dry_run: bool = False
    env: str = "testnet"            # "testnet" (가짜 USDT) | "mainnet" (실거래)

    @classmethod
    def from_env(cls) -> "BinanceTestnetConfig":
        env = os.environ.get("BINANCE_ENV", "testnet").lower()
        if env == "mainnet":
            api_key = os.environ.get("BINANCE_API_KEY", "")
            api_secret = os.environ.get("BINANCE_API_SECRET", "")
            if not api_key or not api_secret:
                # mainnet 키 없으면 testnet 으로 fallback
                logger.warning("[Binance] BINANCE_API_KEY 미설정 — testnet 으로 fallback")
                env = "testnet"
        if env == "testnet":
            api_key = os.environ.get("BINANCE_TESTNET_API_KEY", "")
            api_secret = os.environ.get("BINANCE_TESTNET_API_SECRET", "")
        return cls(
            api_key=api_key,
            api_secret=api_secret,
            market=os.environ.get("BINANCE_MARKET", "futures"),
            dry_run=os.environ.get("BINANCE_DRY_RUN", "false").lower() == "true",
            env=env,
        )


class BinanceTestnetClient:
    """KIS client 와 비슷한 thin wrapper.

    선물 (USDT-M perpetual) 에 집중. spot 도 일부 함수 지원.
    """

    def __init__(self, cfg: BinanceTestnetConfig):
        from binance.client import Client
        self.cfg = cfg
        if not (cfg.api_key and cfg.api_secret):
            raise ValueError(f"Binance {cfg.env} API key/secret not set")
        is_testnet = (cfg.env != "mainnet")
        self._client = Client(cfg.api_key, cfg.api_secret, testnet=is_testnet)
        if is_testnet:
            # testnet futures URL override (python-binance 의 testnet=True 가 spot 만 잡음)
            self._client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
            self._client.FUTURES_TESTNET_URL = "https://testnet.binancefuture.com/fapi"
        else:
            logger.warning(f"[Binance] LIVE mainnet 활성화 — 진짜 돈 매매")
        # 로컬 시계와 Binance 서버 시계 동기화 (-1021 timestamp 에러 방지).
        # Windows 시계가 자주 어긋나는데 Binance 는 +1초만 허용해서 봇이 부팅 못 함.
        self._sync_server_time()

    def _sync_server_time(self):
        """Binance 서버 시각 받아서 client.timestamp_offset 에 보정 적용.

        python-binance 의 모든 signed 요청은 timestamp = time.time()*1000 + timestamp_offset
        을 보내므로 여기서 offset 만 맞춰주면 로컬 시계 어긋남이 다 해결됨.
        """
        try:
            before = int(time.time() * 1000)
            server_time = self._client.futures_time()["serverTime"]
            after = int(time.time() * 1000)
            local_mid = (before + after) // 2
            offset = server_time - local_mid
            self._client.timestamp_offset = offset
            if abs(offset) > 1000:
                logger.warning(
                    f"[Binance] 로컬 시계가 서버와 {offset}ms 어긋남 → 자동 보정 적용")
            else:
                logger.info(f"[Binance] time sync OK (offset {offset}ms)")
        except Exception as e:
            logger.warning(f"[Binance] server time sync fail: {e}; offset=0")
            self._client.timestamp_offset = 0

    # ---------------------------------------------------------------
    # Queries
    # ---------------------------------------------------------------
    def quote(self, symbol: str = "BTCUSDT") -> dict:
        """현재가 (futures-mark or spot-last)."""
        if self.cfg.market == "futures":
            t = self._client.futures_symbol_ticker(symbol=symbol)
            mark = self._client.futures_mark_price(symbol=symbol)
            return {
                "symbol": symbol,
                "last_price": float(t["price"]),
                "mark_price": float(mark["markPrice"]),
                "funding_rate": float(mark.get("lastFundingRate", 0.0)),
                "ts": int(t.get("time", time.time() * 1000)),
            }
        else:
            t = self._client.get_symbol_ticker(symbol=symbol)
            return {
                "symbol": symbol,
                "last_price": float(t["price"]),
                "ts": int(time.time() * 1000),
            }

    def klines(self, symbol: str = "BTCUSDT", interval: str = "1m",
                limit: int = 100):
        """최근 분봉. interval = '1m' | '5m' | '15m' | '1h' | '4h' | '1d'."""
        if self.cfg.market == "futures":
            return self._client.futures_klines(symbol=symbol, interval=interval, limit=limit)
        return self._client.get_klines(symbol=symbol, interval=interval, limit=limit)

    def balance(self) -> dict:
        """선물 계정 잔고 — 가짜 USDT 보유량 등."""
        if self.cfg.market == "futures":
            acc = self._client.futures_account()
            return {
                "total_wallet_balance": float(acc.get("totalWalletBalance", 0)),
                "total_unrealized_pnl": float(acc.get("totalUnrealizedProfit", 0)),
                "total_margin_balance": float(acc.get("totalMarginBalance", 0)),
                "available_balance": float(acc.get("availableBalance", 0)),
                "max_withdraw_amount": float(acc.get("maxWithdrawAmount", 0)),
                "assets": [
                    {
                        "asset": a["asset"],
                        "wallet_balance": float(a["walletBalance"]),
                        "unrealized_pnl": float(a["unrealizedProfit"]),
                        "available": float(a["availableBalance"]),
                    }
                    for a in acc.get("assets", [])
                    if float(a.get("walletBalance", 0)) != 0
                ],
                "positions": [
                    {
                        "symbol": p["symbol"],
                        "qty": float(p["positionAmt"]),
                        "entry_price": float(p["entryPrice"]),
                        "mark_price": float(p.get("markPrice", 0)),  # account 응답엔 없음 (position_info 에만)
                        "unrealized_pnl": float(p["unrealizedProfit"]),
                        "leverage": int(p["leverage"]),
                        "notional": float(p.get("notional", 0)),
                        "side": "LONG" if float(p["positionAmt"]) > 0 else (
                            "SHORT" if float(p["positionAmt"]) < 0 else "FLAT"
                        ),
                    }
                    for p in acc.get("positions", [])
                    if float(p.get("positionAmt", 0)) != 0
                ],
            }
        else:
            acc = self._client.get_account()
            return {
                "balances": [
                    {"asset": b["asset"], "free": float(b["free"]),
                     "locked": float(b["locked"])}
                    for b in acc.get("balances", [])
                    if float(b.get("free", 0)) > 0 or float(b.get("locked", 0)) > 0
                ],
            }

    # ---------------------------------------------------------------
    # Orders (GUARDED)
    # ---------------------------------------------------------------
    def order(
        self,
        symbol: str,
        qty: float,
        side: str,                    # "buy" | "sell"
        order_type: str = "MARKET",   # "MARKET" | "LIMIT"
        price: float | None = None,
        reduce_only: bool = False,
    ) -> dict:
        """선물 주문. dry_run=True 시 mock."""
        side_upper = side.upper()
        if side_upper == "BUY":
            side_b = "BUY"
        elif side_upper == "SELL":
            side_b = "SELL"
        else:
            raise ValueError(f"side must be buy/sell, got {side}")

        if self.cfg.dry_run:
            return {
                "mocked": True, "symbol": symbol, "qty": qty, "side": side_b,
                "type": order_type, "price": price, "ts": time.time(),
            }

        kwargs = {
            "symbol": symbol,
            "side": side_b,
            "type": order_type,
            "quantity": qty,
        }
        if order_type == "LIMIT":
            if price is None:
                raise ValueError("LIMIT order requires price")
            kwargs["timeInForce"] = "GTC"
            kwargs["price"] = price
        if reduce_only:
            kwargs["reduceOnly"] = True

        if self.cfg.market == "futures":
            return self._client.futures_create_order(**kwargs)
        return self._client.create_order(**kwargs)

    def set_leverage(self, symbol: str, leverage: int = 1) -> dict:
        """선물 종목 레버리지 변경 (default 20x → 1x 로 보수적)."""
        if self.cfg.market != "futures":
            raise ValueError("leverage only for futures")
        return self._client.futures_change_leverage(symbol=symbol, leverage=leverage)

    def position(self, symbol: str | None = None) -> list[dict]:
        """현 포지션. symbol 지정 안 하면 전체."""
        if self.cfg.market != "futures":
            raise ValueError("positions only for futures")
        positions = self._client.futures_position_information(
            symbol=symbol) if symbol else self._client.futures_position_information()
        return [
            {
                "symbol": p["symbol"],
                "qty": float(p["positionAmt"]),
                "entry": float(p["entryPrice"]),
                "mark": float(p.get("markPrice", 0) or 0),
                "unrealized_pnl": float(p.get("unRealizedProfit", 0) or 0),
                # leverage 필드는 Binance API 응답에서 빠질 수 있음 (futures_position_information v3)
                "leverage": int(p.get("leverage") or 0),
            }
            for p in positions
            if float(p.get("positionAmt", 0)) != 0
        ]


__all__ = ["BinanceTestnetConfig", "BinanceTestnetClient"]
