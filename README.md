# Crypto Auto-Trading Bot

> Binance USDT-M Perpetual Futures 자동매매 시스템. 4h MA-crossover 추세추종 전략 + walk-forward 검증 + 운영 패치 누적. PC 데스크톱 앱(.exe)·iOS/Android 모바일 앱 포함.

<sub>**English summary**: Multi-component crypto trading system on Binance Futures. MA-crossover long-short swing strategy with trail-stop exit, backtest-verified across 365d × 18 symbols. Includes FastAPI backend, responsive web dashboard, Windows desktop launcher (PyInstaller), and Flutter mobile app for iOS/Android. Production-grade patches: state reconciliation, reduceOnly fallback, time sync, engine state persistence.</sub>

---

## 핵심 결과 — Backtest

| 지표 | Baseline (현재 운영) | Cooldown_1 (다음 후보) |
|---|---:|---:|
| 365일 18종 평균 Sharpe | 3.26 | **9.99** |
| 평균 30일 수익 | +8.6% | +70.8% |
| 평균 Max Drawdown | -3.9% | -6.8% |
| 윈도우 양수 비율 | 85% | 97.5% |
| 비용 sensitivity (4 → 30 bps) | robust | robust |

> 결과 수치는 **2025-06-09 ~ 2026-06-08 365일 × 18 메이저 알트 × 30일 비중첩 walk-forward × 198 윈도우** 기준. 비용 모형은 보수적 cost sensitivity sweep 으로 4 ~ 30 bps/side 까지 robustness 검증.

자세한 검증 과정 → [`docs/`](docs/) 의 HTML 보고서 6편.

---

## 시스템 구조

```
                           ┌─────────────────────────────┐
                           │  FastAPI 백엔드 (app/server.py) │
   PC 데스크톱 (.exe) ──── │   └ BotEngine (검증된 봇 루프)  │ ──▶ Binance Futures
   (pywebview 창)          │   └ 실거래 키(.env)는 여기만   │      (testnet / mainnet)
                           │   REST + WebSocket + 토큰인증  │
   Flutter 모바일 (폰) ─── │                              │
   (iOS / Android)         └─────────────────────────────┘
```

- **백엔드**: FastAPI + WebSocket. 봇 엔진을 스레드로 띄우고 모니터링·시작/정지 API 제공.
- **PC 앱**: pywebview 로 네이티브 창 + 같은 백엔드. PyInstaller 로 `.exe` 단일 배포.
- **모바일 앱**: Flutter (Dart). 같은 WiFi 의 PC 백엔드에 토큰 인증으로 접속.
- **실거래 키는 PC(서버) 한 곳에만 저장**. 모바일은 토큰만으로 원격 제어.

자세한 빌드/배포 → [`app/README.md`](app/README.md), [`mobile/README.md`](mobile/README.md)

---

## 검증 방법론 — Walk-Forward + Cost Sensitivity + Per-Symbol Consistency

단일 백테스트 결과에 의존하지 않고 **3 단계 검증**:

### 1. Walk-Forward (시간/국면 robustness)
365일을 30일 비중첩 윈도우 11개로 분할 → 윈도우별 metric 분리 측정 → **시장 국면 (STRONG_TREND / WEAK_TREND / RANGE) 으로 분류** → regime dependence 확인.
- 결과: 모든 국면에서 cooldown_1 우세 (Sharpe 8.4 ~ 11.8)

### 2. Cost Sensitivity (비용 가정 robustness)
거래 비용을 4 bps → 30 bps/side 까지 6 단계 sweep → 각 비용에서 우승 정책 변하는지 검증.
- 결과: 30 bps (Binance 실거래 6배 보수) 까지 cooldown_1 우세 유지 (Δsharpe +5.7)

### 3. Per-Symbol Consistency (종목 의존성 검증)
18 종목 각각에 대해 정책 효과 분리 측정 → 일부 종목만 좋은 게 아닌지 확인.
- 결과: 18/18 종목에서 cooldown_1 우세, 윈도우 승률 81~100%

> "한 backtest 결과가 좋다 = 채택" 이 아니라 **시간·비용·종목 3축 검증 통과 후 채택**.

---

## 운영 결과 — Testnet 12일

```
2026-05-28 14:00 KST 시작 wallet  $10,817.89
2026-06-08         현재  wallet   $11,727.62  →  +$909.73 (+8.40%)
```

12일 운영 중 발생한 4 건의 production issue 와 해결:

| Issue | 원인 | 패치 |
|---|---|---|
| BNB SHORT stuck 17시간 (235회 거부) | testnet 의 reduceOnly + 100% 마진 사용 시 `-2022` quirk | reduceOnly → plain MARKET 자동 fallback |
| 봇 인식 ≠ 거래소 실체 (state desync) | 청산 주문 실패 시 엔진이 상태를 0 으로 갱신하는 버그 | 매 tick 시작 시 `_reconcile()` 자동 동기화 |
| 재시작 시 trail 누적 데이터 손실 | 메모리 only `low_water/high_water` | `engine_state.json` 영속화 + load/save |
| 봇 부팅 즉시 종료 (`-1021`) | 로컬 시계가 Binance 서버보다 +7.8초 어긋남 | `client.timestamp_offset` 자동 동기화 (mainnet 호환) |

자세한 분석 → [`docs/2026-05-28/swing_walkforward.html`](docs/2026-05-28/swing_walkforward.html), [`docs/2026-05-30/tp_walkforward.html`](docs/2026-05-30/tp_walkforward.html) 등.

---

## 빠른 시작

```powershell
# 1. .env 에 Binance Testnet API 키 설정
# BINANCE_TESTNET_API_KEY=xxx
# BINANCE_TESTNET_API_SECRET=xxx

# 2. PC 데스크톱 앱 (네이티브 창 + 봇 + 대시보드)
.\run_app.ps1

# 또는 봇만 (헤드리스)
.\run_swing_bot.ps1
```

배포용 `.exe` 빌드:
```powershell
pip install pyinstaller pywebview
pyinstaller crypto_bot.spec
# → dist/CryptoBot/CryptoBot.exe (29MB)
```

폰 앱 빌드 → [`mobile/README.md`](mobile/README.md)

---

## 보고서 (`docs/`)

검증 과정을 시간순으로 HTML 로 정리. 모든 보고서는:
- 인라인 SVG 차트 (외부 의존 없는 단일 파일)
- 비전공자도 끝까지 읽을 수 있는 서술
- 용어 정리집 (Glossary) 포함

| 날짜 | 제목 | 핵심 결과 |
|---|---|---|
| 2026-05-22 | [swing 모드 도입 (옵션 A/B)](docs/2026-05-22/crypto_swing_mode_AB.html) | 4h+12/48+Trail2 가 1h+6/24 보다 우수 (Sharpe +6.29) |
| 2026-05-22 | [알트 확장 5종 → 18종](docs/2026-05-22/crypto_swing_alts_expansion.html) | 단일 룰로 18종 일괄 운영 가능 |
| 2026-05-23 | [옵션 Y/Z + long-short 가설](docs/2026-05-23/swing_optY_optZ_diagnosis.html) | Live -$300 손실 → long-short 적용으로 backtest 동조 |
| 2026-05-28 | [Long-short walk-forward](docs/2026-05-28/swing_walkforward.html) | 11/11 윈도우 모두 +수익 |
| 2026-05-28 | [Trend vs Mean-Reversion](docs/2026-05-28/trend_vs_mr_walkforward.html) | 모든 변동성 국면에서 trend 우세, MR 추가 불필요 |
| 2026-05-30 | [TP / tiered trail 검증](docs/2026-05-30/tp_walkforward.html) | tiered+3% 14/18 종목 부분 채택 권장 |

---

## 디렉토리 구조

```
crypto/
  ├ scripts/                 # 운영 + 백테스트 스크립트
  │   ├ run_crypto_testnet.py        # ★ 운영 봇 진입점
  │   ├ close_crypto_positions.py    # 전 포지션 시장가 청산
  │   ├ backtest_crypto_*.py         # 백테스트 (10+ 변형)
  │   └ _make_dashboard_data.py      # 운영 대시보드 데이터 수집
  ├ lib/autotrader/          # 봇 코어 (재사용 가능 라이브러리)
  │   ├ live/engine.py               # ★ 스레드 제어형 BotEngine
  │   ├ broker/binance_testnet_client.py   # Binance Futures wrapper
  │   ├ backtest/crypto_strategies.py      # 백테스트 엔진 + overlay
  │   └ data/crypto_bars.py                # OHLCV 캐시
  ├ app/                     # PC 앱 + 백엔드
  │   ├ server.py                    # FastAPI (REST + WebSocket + 토큰인증)
  │   ├ manager.py                   # 봇 생명주기 관리
  │   ├ desktop.py                   # pywebview 진입점
  │   ├ presets.py                   # 검증된 운영 구성 화이트리스트
  │   └ web/                         # 반응형 대시보드 (HTML/CSS/JS)
  ├ mobile/                  # Flutter 앱 (iOS/Android)
  │   └ lib/                         # Dart 소스 (api_client / dashboard / theme)
  ├ docs/                    # 작업 보고서 (HTML, 시간순)
  ├ data/                    # OHLCV 캐시 + 백테스트 결과 (대부분 gitignore)
  ├ crypto_bot.spec          # PyInstaller 빌드 정의
  └ .env                     # Binance API 키 (gitignore — 절대 커밋 X)
```

---

## 운영 봇 인자

```powershell
python scripts/run_crypto_testnet.py `
  --mode swing --interval 4h --fast 12 --slow 48 `
  --trail-pct 0.02 --allow-short `
  --block-policy cooldown --cooldown-bars 1 `
  --symbols BTCUSDT,ETHUSDT,...,ATOMUSDT `
  --leverage 1 --position-pct 0.95 `
  --poll-min 5 --duration-hours 0
```

| 인자 | 의미 | 권장값 |
|---|---|---|
| `--mode` | `swing` (trail 청산) / `intraday` (신호 반전 청산) | swing |
| `--interval` | 신호 계산 봉 간격 | 4h |
| `--fast` / `--slow` | MA 봉 수 (4h × 12/48 = 2일/8일 평균선) | 12 / 48 |
| `--trail-pct` | trailing stop 폭 (peak 대비) | 0.02 (2%) |
| `--allow-short` | SHORT 허용 (long-short 양방향) | 켜짐 |
| `--block-policy` | trail-stop 후 재진입 차단 정책 | `cooldown` (또는 `signal_change`) |
| `--cooldown-bars` | cooldown 봉 수 | 1 (4h) |
| `--leverage` | 레버리지 (높이면 청산 위험 ↑) | **1** |
| `--position-pct` | 자본 중 포지션 비율 | 0.95 |
| `--poll-min` | 폴링 주기 (분) | 5 |
| `--duration-hours` | 운영 시간 (`0` = 무한) | 0 |

## 모니터링 / 종료

```powershell
# 실시간 로그
Get-Content -Path data\crypto_runner_*.log -Wait -Tail 20

# 현재 잔고
python scripts/close_crypto_positions.py --dry-run

# 봇 graceful 종료 (포지션 유지)
Get-Process python | Where-Object { $_.CommandLine -like "*run_crypto_testnet*" } | Stop-Process

# 전 포지션 시장가 청산
python scripts/close_crypto_positions.py
```

## 백테스트 실행

```powershell
# Walk-forward (시간 robustness)
python scripts/backtest_crypto_walkforward.py --days 365 --window-days 30

# 비용 sensitivity sweep
python scripts/backtest_crypto_block_costs.py --days 365

# 종목별 일관성 검증
python scripts/backtest_crypto_block_per_symbol.py --days 365 --cost-bps 10
```

## 주의사항

- `.env` 의 `BINANCE_ENV=testnet` (가짜 USDT) / `mainnet` (실거래) — **반드시 확인 후 가동**
- backtest 는 funding rate / 슬리피지 미반영 — 실제 수익은 다소 낮음
- 새 종목 추가 시 `SYMBOL_QTY_PRECISION` (lib/autotrader/live/engine.py) 에 lot size 등록 필요
- 레버리지 1x 검증 — 높은 레버리지는 backtest 결과 무효 + 청산 위험

## 라이선스 / 사용

개인 학습·포트폴리오 목적 공개. 실거래에 적용 시 발생하는 손실에 대한 책임은 사용자에게 있음. mainnet 전환 전 testnet 충분한 검증 권장.

---

<sub>Made with backtest discipline. 검증되지 않은 것은 운영에 넣지 않는다.</sub>
