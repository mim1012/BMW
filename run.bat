@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHON=D:\Project\.venv\Scripts\python.exe
set PYTHONUTF8=1
%PYTHON% -X utf8 main.py
pause
