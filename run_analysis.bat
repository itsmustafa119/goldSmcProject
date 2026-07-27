@echo off
setlocal

cd /d "%~dp0"
title MT5 Smart Money Concepts Analysis

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERROR: The project virtual environment was not found.
    echo Expected: %~dp0.venv\Scripts\python.exe
    pause
    exit /b 1
)

set "PYTHONIOENCODING=utf-8"
set "SMC_CREDIT=0"

echo ========================================================
echo  MARKET ANALYSIS - SMC INDICATORS
echo ========================================================
echo.
echo MetaTrader 5 must be open and logged in.
echo Choose the instrument and timeframe in this window.
echo.

".venv\Scripts\python.exe" -m gold_smc.launcher analysis %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo ERROR: Analysis stopped with exit code %EXIT_CODE%.
    pause
)

endlocal
exit /b %EXIT_CODE%
