# Crypto 자동매매 (standalone)

ai_pricing 모노레포에서 분리한 코인 자동매매 프로젝트. Binance USDT-M perpetual futures (testnet/mainnet) 대상.

> **앱으로 쓰기**: 봇을 모니터링 + 시작/정지하는 PC 데스크톱 앱(.exe)과 폰 앱(Flutter)이 있다.
> 빌드·실행·연결 방법은 [`app/README.md`](app/README.md), 폰 앱은 [`mobile/README.md`](mobile/README.md).
> 빠른 실행: `.\run_app.ps1` (PC 네이티브 창) / 배포 빌드: `pyinstaller crypto_bot.spec`.

## 디렉토리 구조

```
crypto/
  run_swing_bot.ps1        # 운영 봇 원클릭 재실행 스크립트
  scripts/                 # 운영 + 백테스트 스크립트
    run_crypto_testnet.py      # ★ 운영 봇 (multi-symbol swing/intraday)
    run_crypto_per_symbol.py   # 종목별 차별화 전략 봇
    close_crypto_positions.py  # 전 포지션 시장가 청산
    binance_test_order.py      # 단발 주문 테스트
    backtest_crypto_*.py       # 백테스트 (sweep / walk-forward 등)
  lib/autotrader/          # crypto 전용 모듈 (import autotrader.xxx)
    data/      crypto_bars.py, crypto_signals.py
    backtest/  crypto_strategies.py, crypto_momentum.py
    broker/    binance_testnet_client.py
  data/                    # OHLCV 캐시 + 백테스트 결과 + 운영 로그
  docs/                    # 작업 보고서 (HTML)
  .env                     # Binance API 키 (BINANCE_ENV / TESTNET_API_KEY 등)
```

## 운영 봇 실행

### 현재 운영 설정 — long-short swing (검증된 구성)

```powershell
# 원클릭
.\run_swing_bot.ps1

# 또는 직접
python scripts/run_crypto_testnet.py --mode swing --interval 4h --fast 12 --slow 48 `
  --trail-pct 0.02 --allow-short `
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,AVAXUSDT,BNBUSDT,DOGEUSDT,ADAUSDT,XRPUSDT,DOTUSDT,LINKUSDT,LTCUSDT,BCHUSDT,ARBUSDT,OPUSDT,SUIUSDT,INJUSDT,NEARUSDT,ATOMUSDT `
  --poll-min 5 --duration-hours 0
```

### 인자 설명

| 인자 | 의미 | 현재 값 |
|------|------|---------|
| `--mode` | `swing` (trail 청산) / `intraday` (신호 반전 즉시 청산) | swing |
| `--interval` | 신호 계산용 봉 간격 (1h / 4h / 1d) | 4h |
| `--fast` / `--slow` | 단기/장기 MA 봉 수. 4h×12/48 = 2일/8일 평균선 | 12 / 48 |
| `--trail-pct` | trailing stop 폭 (고점 대비). 0.02 = 2% | 0.02 |
| `--allow-short` | SHORT 진입 허용 (long-short). 없으면 long-only | 켜짐 |
| `--symbols` | 콤마 구분 종목. 18종 (TRX·MATIC 제외) | 18종 |
| `--capital-split` | 콤마 구분 자본 비중. 생략 시 균등 분할 | 균등 |
| `--leverage` | 레버리지 배수 | 1 |
| `--position-pct` | 할당 자본 중 포지션 비율 | 0.95 |
| `--poll-min` | 폴링 주기(분). trail 정밀도. backtest 일치는 신호=봉마감 | 5 |
| `--duration-hours` | 운영 시간. `0` = 무한 (Ctrl+C/SIGTERM 으로만 종료) | 0 |

> 핵심: 신호 진입은 **4h 봉 마감 시점에만** 평가 (옵션 Z), trail 청산도 봉 마감 close 기준. trail 발동 후 같은 방향 신호로는 재진입 차단 (`stopped_until_signal_change`). long-short 라 상승/하락 양방향 추세를 다 잡음 = backtest sweep 우승 구성.

## 모니터링

```powershell
# 실시간 로그
Get-Content -Path data\crypto_runner_*.log -Wait -Tail 20

# 현재 보유 포지션 + 미실현 P&L
python scripts/close_crypto_positions.py --dry-run

# wallet 잔고
python -c "import sys,os; sys.path.insert(0,'lib'); [os.environ.setdefault(*l.split('#')[0].strip().split('=',1)) for l in open('.env',encoding='utf-8') if '=' in l.split('#')[0]]; from autotrader.broker.binance_testnet_client import BinanceTestnetClient,BinanceTestnetConfig; b=BinanceTestnetClient(BinanceTestnetConfig.from_env()).balance(); print('wallet',round(b['total_wallet_balance'],2),'margin',round(b['total_margin_balance'],2))"
```

## 종료 / 청산

```powershell
# 봇 종료 (graceful — 포지션은 binance 에 유지, 재시작 시 자동 인계)
Get-Process python | Where-Object { $_.CommandLine -like "*run_crypto_testnet*" } | Stop-Process

# 전 포지션 청산
python scripts/close_crypto_positions.py
```

## 백테스트

```powershell
# walk-forward (수익이 일시적인가 꾸준한가 — 30일 윈도우)
python scripts/backtest_crypto_walkforward.py --days 365 --window-days 30

# trend vs mean-reversion (변동성 국면별)
python scripts/backtest_crypto_trend_vs_mr_wf.py --days 365

# swing 종목/타임프레임 sweep
python scripts/backtest_crypto_swing_sweep.py --days 365
```

## 보고서

`docs/` 의 HTML 보고서 (시간순):
- `2026-05-22/` — swing 모드 도입 (옵션 A/B), 알트 확장 (19종)
- `2026-05-23/` — 옵션 Y/Z 진단 + long-short 가설 발견
- `2026-05-28/` — walk-forward 분석, trend vs mean-reversion 비교
- `2026-05-30/` — TP / tiered trail 룰 sweep + walk-forward (14/18 종목 부분 채택 권장)

## 주의

- 현재 `.env` 의 `BINANCE_ENV` 가 `testnet` 이면 가짜 USDT. `mainnet` 전환 시 실거래.
- backtest 는 funding rate / 슬리피지 미반영 — 실제 live 수익은 다소 낮을 수 있음.
- 종목 추가 시 `scripts/run_crypto_testnet.py` 의 `SYMBOL_QTY_PRECISION` 테이블에 lot size 등록 필요.
