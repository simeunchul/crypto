# PC 데스크톱 앱 (개발 모드) — 서버 + 네이티브 창
# 배포용 .exe 빌드는:  pyinstaller crypto_bot.spec
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Write-Host "Crypto Bot 데스크톱 앱 시작..." -ForegroundColor Cyan
python run_desktop.py
