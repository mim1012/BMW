@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHON=D:\Project\.venv\Scripts\python.exe
set PYTHONUTF8=1

echo ============================================
echo  BMW 구매 폼 필드 수집 (1회 실행)
echo ============================================
echo.
echo 1. 아래 Chrome 실행 명령이 자동으로 실행됩니다.
echo 2. Chrome 에서 로그인 후 BMW 구매 폼 화면으로 이동하세요.
echo 3. 폼 드롭다운이 보이는 화면에서 Enter 를 누르세요.
echo.

REM Chrome 디버그 모드로 실행
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
    --remote-debugging-port=9222 ^
    --user-data-dir="%~dp0chrome_profile" ^
    --start-maximized ^
    "https://shop.bmw.co.kr"

echo Chrome 이 열렸습니다.
echo BMW 구매 폼 화면(드롭다운 보이는 곳)까지 이동 후 Enter 를 누르세요.
pause

%PYTHON% -X utf8 collect_fields.py
