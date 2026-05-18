@echo off
echo BMW 자동화 환경 설정
echo =====================

cd /d "%~dp0"

:: 프로젝트 루트 venv 사용
set PYTHON=D:\Project\.venv\Scripts\python.exe

%PYTHON% -m pip install -r requirements.txt
%PYTHON% -m playwright install chromium

echo.
echo 설정 완료! run.bat 으로 실행하세요.
pause
