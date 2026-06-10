"""PC 데스크톱 앱 진입점 (PyInstaller entry / 직접 실행 둘 다).

개발 실행:  python run_desktop.py
패키징:     pyinstaller crypto_bot.spec
"""

from app.desktop import main

if __name__ == "__main__":
    main()
