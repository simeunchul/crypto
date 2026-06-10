"""실행 프리셋 — 앱에서 봇을 켤 때 '검증된 구성' 중 골라 시작.

완전 자유 파라미터 대신 검증된 프리셋만 노출 → 실거래 오조작 위험 최소화.
(기능 범위: 모니터링 + 시작/정지)
"""

from __future__ import annotations

ALT18 = ("BTCUSDT,ETHUSDT,SOLUSDT,AVAXUSDT,BNBUSDT,DOGEUSDT,ADAUSDT,XRPUSDT,"
         "DOTUSDT,LINKUSDT,LTCUSDT,BCHUSDT,ARBUSDT,OPUSDT,SUIUSDT,INJUSDT,"
         "NEARUSDT,ATOMUSDT").split(",")

PRESETS: dict[str, dict] = {
    "swing_ls_18": {
        "label": "Long-Short Swing · 18종 (검증 우승 구성)",
        "description": "4h MA 12/48, trail 2%, long-short. backtest sweep 우승 구성.",
        "config": {
            "symbols": ALT18,
            "mode": "swing",
            "interval": "4h",
            "fast": 12,
            "slow": 48,
            "trail_pct": 0.02,
            "allow_short": True,
            "leverage": 1,
            "position_pct": 0.95,
            "poll_min": 5,
            "duration_hours": 0,
        },
    },
    "swing_ls_btceth": {
        "label": "Long-Short Swing · BTC+ETH (보수적 2종)",
        "description": "메이저 2종만. 자본 작을 때 최소 주문액 안전. 4h 12/48 trail 2%.",
        "config": {
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "mode": "swing",
            "interval": "4h",
            "fast": 12,
            "slow": 48,
            "trail_pct": 0.02,
            "allow_short": True,
            "leverage": 1,
            "position_pct": 0.95,
            "poll_min": 5,
            "duration_hours": 0,
        },
    },
    "swing_long_btceth": {
        "label": "Long-Only Swing · BTC+ETH (가장 보수적)",
        "description": "롱만. 하락장 진입 없음. 가장 방어적인 구성.",
        "config": {
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "mode": "swing",
            "interval": "4h",
            "fast": 12,
            "slow": 48,
            "trail_pct": 0.02,
            "allow_short": False,
            "leverage": 1,
            "position_pct": 0.95,
            "poll_min": 5,
            "duration_hours": 0,
        },
    },
}

DEFAULT_PRESET = "swing_ls_btceth"


def list_presets() -> list[dict]:
    return [
        {"key": k, "label": v["label"], "description": v["description"],
         "config": v["config"]}
        for k, v in PRESETS.items()
    ]


def get_preset_config(key: str) -> dict | None:
    p = PRESETS.get(key)
    return dict(p["config"]) if p else None
