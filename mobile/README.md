# Crypto Bot — 네이티브 모바일 앱 (Flutter)

PC 데스크톱 앱(FastAPI 서버)에 같은 와이파이로 접속해 봇을 **모니터링 + 시작/정지**하는
iOS/Android 네이티브 앱. 하나의 Dart 코드베이스로 두 플랫폼 모두 빌드.

> 실거래 키는 PC(서버)에만 있고 폰엔 저장되지 않는다. 폰은 토큰으로 서버에 접속만 한다.

## 사전 준비

1. [Flutter SDK](https://docs.flutter.dev/get-started/install) 설치 (`flutter --version` 확인)
2. Android: Android Studio + SDK / iOS: **macOS + Xcode** (iOS 빌드는 Mac 필수)

## 프로젝트 생성 (최초 1회)

이 폴더엔 앱 소스(`lib/`, `pubspec.yaml`)만 들어있다. 플랫폼 폴더(android/ios)는
아래 명령으로 생성한다 — `mobile/` 폴더 안에서 실행:

```bash
cd mobile
flutter create --org com.cryptobot --project-name crypto_bot_mobile .
flutter pub get
```

`flutter create .` 는 기존 `lib/`, `pubspec.yaml` 을 덮어쓰지 않고 android/ios 등
빠진 플랫폼 파일만 채운다. (덮어쓴다면 git 으로 lib/, pubspec.yaml 복원)

## 실행 / 빌드

```bash
# 연결된 기기/에뮬레이터에서 실행
flutter run

# Android 설치 파일 (배포용)
flutter build apk --release          # → build/app/outputs/flutter-apk/app-release.apk

# iOS (Mac 에서만)
flutter build ios --release          # 이후 Xcode 로 서명·아카이브
```

## 사용

1. PC 데스크톱 앱(CryptoBot.exe) 실행 → "📱 폰 앱 연결 정보" 버튼 → **서버 주소 + 토큰** 확인
2. 폰 앱 첫 화면에 그 주소(`http://192.168.x.x:8787`)와 토큰 입력 → 연결
3. 대시보드에서 프리셋 선택 후 시작/정지, 잔고·포지션·로그 실시간 확인

> PC 와 폰이 **같은 와이파이**에 있어야 한다. (서버는 0.0.0.0 바인딩, 토큰 인증)
> 외부망에서 쓰려면 별도 포트포워딩/VPN 필요 — 보안상 권장하지 않음.
