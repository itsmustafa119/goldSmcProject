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
echo.
echo Starting the analysis and launching your browser...
echo.

REM Run the Python script
".venv\Scripts\python.exe" "analyze_gold_mt5.py"

set "EXIT_CODE=%ERRORLEVEL%"

if "%EXIT_CODE%"=="0" (
    echo.
    echo Dashboard stopped normally. Please close this window.
    timeout /t 3 /nobreak
) else if "%EXIT_CODE%"=="1" (
    echo.
    echo Dashboard is already running (only one instance allowed at a time^).
    echo.
    echo To use the dashboard, stop the currently running instance first.
    pause
) else (
    echo.
    echo ERROR: The dashboard stopped unexpectedly ^(exit code %EXIT_CODE%^).
    echo.
    echo Please check:
    echo - MetaTrader 5 is open and logged in
    echo - Your internet connection is working
    echo - No other instance is running
    pause
)

endlocal
exit /b %EXIT_CODE%
