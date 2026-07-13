@echo off
setlocal

cd /d "%~dp0"
title XAUUSD Smart Money Concepts - Live Dashboard

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERROR: The project virtual environment was not found.
    echo Expected: %~dp0.venv\Scripts\python.exe
    echo.
    echo Create the environment and install the project dependencies first.
    pause
    exit /b 1
)

if not exist "analyze_gold_mt5.py" (
    echo.
    echo ERROR: analyze_gold_mt5.py was not found in this folder.
    pause
    exit /b 1
)

set "PYTHONIOENCODING=utf-8"
set "SMC_CREDIT=0"

echo ========================================================
echo  XAUUSD M15 Smart Money Concepts - Live Dashboard
echo ========================================================
echo.
echo Keep MetaTrader 5 open and logged in.
echo The dashboard will open automatically in your browser.
echo Press Ctrl+C in this window when you want to stop it.
echo.

".venv\Scripts\python.exe" "analyze_gold_mt5.py"

set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo The dashboard stopped with exit code %EXIT_CODE%.
    pause
)

endlocal
exit /b %EXIT_CODE%
