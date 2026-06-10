"""Binance Futures Testnet — small test order (0.002 BTC round-trip).

검증 사항:
  1) 레버리지 1x 설정 (안전)
  2) 시장가 매수
  3) 포지션 / mark price 확인
  4) 즉시 시장가 매도 (reduceOnly)
  5) 최종 잔고 + P&L

가짜 USDT 환경. 실돈 X.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.split("#", 1)[0].strip()
    if "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from autotrader.broker.binance_testnet_client import (
    BinanceTestnetClient, BinanceTestnetConfig,
)

SYMBOL = "BTCUSDT"
QTY = 0.002          # 0.002 BTC ≈ $154 (min notional $100 보장)
LEVERAGE = 1         # 1x = no leverage 안전

c = BinanceTestnetClient(BinanceTestnetConfig.from_env())

# 1. 레버리지 설정
print(f"=== Step 1: 레버리지 {LEVERAGE}x 설정 ===")
r = c.set_leverage(SYMBOL, LEVERAGE)
print(f"  ✓ {r.get('symbol')} leverage={r.get('leverage')}x maxNotional={r.get('maxNotionalValue')}")

# 2. 매수 전 시세
print()
print(f"=== Step 2: 매수 전 시세 / 잔고 ===")
q = c.quote(SYMBOL)
b0 = c.balance()
print(f"  mark_price: {q['mark_price']:>12,.2f} USDT")
print(f"  wallet:     {b0['total_wallet_balance']:>12,.2f} USDT")
print(f"  available:  {b0['available_balance']:>12,.2f} USDT")
notional = QTY * q['mark_price']
print(f"  주문 명목가: {notional:>12,.2f} USDT  (qty={QTY} BTC × mark)")

# 3. 시장가 매수
print()
print(f"=== Step 3: 시장가 매수 BTC {QTY} ===")
r1 = c.order(SYMBOL, qty=QTY, side="buy", order_type="MARKET")
print(f"  orderId: {r1.get('orderId')}")
print(f"  status:  {r1.get('status')}")
print(f"  side:    {r1.get('side')} qty={r1.get('origQty')} avgPrice={r1.get('avgPrice', 'TBD')}")

time.sleep(2)

# 4. 포지션 확인
print()
print(f"=== Step 4: 포지션 확인 (3초 후) ===")
b1 = c.balance()
for p in b1["positions"]:
    print(f"  {p['symbol']:>10s}  {p['side']:>5s}  qty={p['qty']:>8.4f}  entry={p['entry_price']:>10,.2f}  mark={p['mark_price']:>10,.2f}  upnl={p['unrealized_pnl']:>+10,.4f}  lev={p['leverage']}x")
print(f"  wallet:    {b1['total_wallet_balance']:>12,.4f} USDT")
print(f"  available: {b1['available_balance']:>12,.4f} USDT")

# 5. 즉시 매도 (reduceOnly)
print()
print(f"=== Step 5: 시장가 매도 (reduceOnly) ===")
r2 = c.order(SYMBOL, qty=QTY, side="sell", order_type="MARKET", reduce_only=True)
print(f"  orderId: {r2.get('orderId')}")
print(f"  status:  {r2.get('status')}")
print(f"  side:    {r2.get('side')} qty={r2.get('origQty')}")

time.sleep(3)

# 6. 최종 잔고 + P&L
print()
print(f"=== Step 6: 최종 잔고 ===")
b2 = c.balance()
realized_pnl = b2['total_wallet_balance'] - b0['total_wallet_balance']
print(f"  wallet:        {b2['total_wallet_balance']:>12,.4f} USDT  (시작 {b0['total_wallet_balance']:,.4f})")
print(f"  realized P&L:  {realized_pnl:>+12,.4f} USDT  ({realized_pnl/b0['total_wallet_balance']*100:+.4f}%)")
print(f"  positions:     {len(b2['positions'])} (0 이어야 정상 청산)")
for p in b2["positions"]:
    print(f"    잔여: {p['symbol']} qty={p['qty']:.4f} side={p['side']}")

print()
print("✓ Round-trip 매매 flow 검증 완료." if not b2["positions"] else "⚠ 포지션 잔존 — 확인 필요")
