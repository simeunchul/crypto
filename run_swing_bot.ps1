# ============================================================
# Crypto Swing 운영 봇 — 원클릭 재실행 스크립트
# ============================================================
# 전략: long-short 4h봉 + 12/48 MA + trail-2% (옵션 Z + 차단 플래그)
# 종목: 18종 (TRX, MATIC 제외)
# 모드: 무한 (Ctrl+C 또는 SIGTERM 으로만 종료)
#
# 사용법:
#   powershell -ExecutionPolicy Bypass -File run_swing_bot.ps1
# 또는 PowerShell 에서:
#   .\run_swing_bot.ps1
#
# 백그라운드 (로그 파일로):
#   Start-Process python -ArgumentList "scripts/run_crypto_testnet.py ..." -RedirectStandardOutput data/bot.log
# ============================================================

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot   # crypto/ 디렉토리에서 실행 (ROOT 경로 보장)

$symbols = "BTCUSDT,ETHUSDT,SOLUSDT,AVAXUSDT,BNBUSDT,DOGEUSDT,ADAUSDT,XRPUSDT,DOTUSDT,LINKUSDT,LTCUSDT,BCHUSDT,ARBUSDT,OPUSDT,SUIUSDT,INJUSDT,NEARUSDT,ATOMUSDT"

python scripts/run_crypto_testnet.py `
  --mode swing `
  --interval 4h `
  --fast 12 --slow 48 `
  --trail-pct 0.02 `
  --allow-short `
  --symbols $symbols `
  --poll-min 5 `
  --duration-hours 0
