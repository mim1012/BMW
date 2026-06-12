@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: Python 경로 결정
if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=python
)

echo [1/3] 의존성 설치...
"%PYTHON%" -m pip install -r requirements.txt --quiet
if errorlevel 1 goto :error

"%PYTHON%" -m pip install pyinstaller --quiet
if errorlevel 1 goto :error

echo [2/3] PyInstaller 빌드...
"%PYTHON%" -m PyInstaller BMW-AutoBuyer.spec --noconfirm --clean
if errorlevel 1 goto :error

echo [3/3] 빌드 완료
echo.
echo 실행 파일: %~dp0dist\BMW-AutoBuyer.exe
echo.
goto :end

:error
echo [오류] 빌드 실패
exit /b 1

:end
endlocal
