@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if "%~1"=="" (
    echo Usage: run.bat BV1xxxxxxxxx
    echo Example: run.bat BV1U1nF6PE7e
    pause
    exit /b 1
)
if not exist "out" mkdir "out"
python tools\bili_bot_filter.py --bv %1 --pages 2 --out "out\report.md"
if errorlevel 1 (
    echo FAILED
    pause
    exit /b 1
)
echo.
echo Report written to out\report.md
pause
