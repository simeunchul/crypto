"""Binance Futures testnet — 모든 미결 포지션 시장가 청산.

운영 모드 교체 (intraday → swing, 또는 종목 변경) 전에 깨끗한 시작을 위해 1회 실행.
실거래 (mainnet) 키가 잡혀있으면 진짜 청산되므로 confirm prompt 통과 필요.

Usage:
  python scripts/close_crypto_positions.py                 # 모든 종목 청산
  python scripts/close_crypto_positions.py --dry-run       # 청산 안 함, 보유만 표시
  python scripts/close_crypto_positions.py --symbols BTCUSDT,ETHUSDT  # 특정 종목만
"""
from __future__ import annotations

import argparse
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="실제 청산 안 함, 보유만 표시")
    ap.add_argument("--symbols", default=None, help="콤마 구분 종목 (default: 모든 미결)")
    args = ap.parse_args()

    cfg = BinanceTestnetConfig.from_env()
    c = BinanceTestnetClient(cfg)

    print(f"=== Close all positions ({cfg.env}) ===")
    bal = c.balance()
    open_positions = [p for p in bal["positions"] if abs(p["qty"]) > 0]

    if args.symbols:
        wanted = {s.strip() for s in args.symbols.split(",") if s.strip()}
        open_positions = [p for p in open_positions if p["symbol"] in wanted]

    if not open_positions:
        print("  미결 포지션 없음.")
        return

    print(f"  미결 포지션 {len(open_positions)}개:")
    for p in open_positions:
        print(f"    {p['symbol']:>12s} side={p['side']:>5} qty={p['qty']:>12.4f} "
              f"entry={p['entry_price']:>10,.4f} upnl={p['unrealized_pnl']:+.4f}")

    if args.dry_run:
        print("\n  [dry-run] 실제 청산 안 함.")
        return

    if cfg.env == "mainnet":
        print("\n  ⚠ MAINNET — 실거래 청산입니다.")
        confirm = input("  계속하려면 'YES' 입력: ").strip()
        if confirm != "YES":
            print("  취소.")
            return

    print()
    closed = 0
    failed = 0
    for p in open_positions:
        sym = p["symbol"]
        side_close = "sell" if p["qty"] > 0 else "buy"
        qty = abs(p["qty"])
        try:
            r = c.order(sym, qty=qty, side=side_close,
                         order_type="MARKET", reduce_only=True)
            print(f"  [{sym}] CLOSE {side_close} {qty:.4f} → orderId={r.get('orderId')}")
            closed += 1
            time.sleep(0.4)
        except Exception as e:
            print(f"  [{sym}] close FAIL: {e}")
            failed += 1

    print()
    print(f"  완료: {closed} 성공, {failed} 실패")
    bal_after = c.balance()
    open_after = [p for p in bal_after["positions"] if abs(p["qty"]) > 0]
    print(f"  남은 미결: {len(open_after)}개")
    for p in open_after:
        print(f"    {p['symbol']:>12s} qty={p['qty']:>10.4f}")


if __name__ == "__main__":
    main()
