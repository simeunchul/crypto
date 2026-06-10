# Crypto Bot — 앱 (PC 데스크톱 + 모바일)

기존 Python 봇을 **하나의 공유 백엔드**로 감싸고, 그 위에 PC 데스크톱 앱과
폰 앱(Flutter)이 붙는 구조. 기능은 **모니터링 + 시작/정지**.

```
                    ┌─────────────────────────────┐
   PC 데스크톱 앱   │  FastAPI 서버 (app/server.py)│
   (pywebview 창) ──┤   └ BotEngine (검증된 봇 루프) │──▶ Binance (testnet/mainnet)
                    │   └ 실거래 키(.env)는 여기만   │
   폰 앱 (Flutter) ─┤   REST + WebSocket + 토큰인증 │
   같은 와이파이    └─────────────────────────────┘
```

실거래 API 키는 **PC(서버)** 의 `.env` 에만 있고 폰엔 저장되지 않는다.
폰은 토큰으로 서버에 접속만 한다.

## 구성요소

| 파일 | 역할 |
|------|------|
| `lib/autotrader/live/engine.py` | 스레드 제어형 봇 엔진 (CLI·서버 공유) |
| `app/server.py` | FastAPI 백엔드 (status/balance/positions/logs/start/stop, WS) |
| `app/manager.py` | 봇 1개의 생명주기(start/stop) + read-only 조회 |
| `app/presets.py` | 검증된 실행 프리셋 (오조작 방지용 화이트리스트) |
| `app/web/` | 반응형 웹 대시보드 (PC 창이 로드) |
| `app/desktop.py` | PC 앱 진입점 (서버 스레드 + 네이티브 창) |
| `crypto_bot.spec` | PyInstaller 배포 빌드 정의 |
| `mobile/` | Flutter 네이티브 앱 (iOS/Android) — `mobile/README.md` 참고 |

## PC 앱

### 개발 모드 실행
```powershell
.\run_app.ps1
# 또는
python run_desktop.py
```
서버가 백그라운드로 뜨고 네이티브 창이 열린다. 콘솔에 폰 연결용 주소·토큰 출력.

서버만 띄우고 브라우저로 보려면:
```powershell
python -m app.server --host 0.0.0.0 --port 8787
```

### 배포용 .exe 빌드
```powershell
pyinstaller crypto_bot.spec
```
결과: `dist/CryptoBot/` 폴더 (CryptoBot.exe + 동봉 리소스).

**배포**: `dist/CryptoBot/` 폴더를 통째로 전달. 받는 사람은
1. 그 폴더에 `.env` 파일을 넣고 (Binance 키 설정)
2. `CryptoBot.exe` 실행
→ 로그·토큰은 같은 폴더의 `data/` 에 생성된다.

## 폰 앱 연결

1. PC 앱에서 **📱 폰 앱 연결 정보** 버튼 → 서버 주소(`http://192.168.x.x:8787`)와 토큰 확인
2. 폰 앱 첫 화면에 입력 → 연결
3. PC 와 폰이 **같은 와이파이** 여야 함

빌드 방법은 `mobile/README.md`.

## 보안 모델

- `127.0.0.1`(PC 본인) 요청 → 무인증 (데스크톱 앱 편의)
- 그 외 IP(폰) → **Bearer 토큰 필수**. 토큰은 `CRYPTO_API_TOKEN` env 또는 `data/.api_token` 자동생성
- 시작 가능한 구성은 `presets.py` 화이트리스트로 제한 → 임의 파라미터 주입 차단
- MAINNET(실거래) 환경에서 시작 시 PC·폰 모두 **실거래 확인 모달** 표시

## 실거래 전환

`.env` 에 아래 3줄:
```
BINANCE_ENV=mainnet
BINANCE_API_KEY=<실거래 키>
BINANCE_API_SECRET=<실거래 시크릿>
```
> 실돈 전 점검: 계정 One-way(단방향) 모드, 최소 주문액, Futures 권한. 메인 README 주의사항 참고.
