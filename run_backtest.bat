@echo off
setlocal

cd /d "%~dp0"
title MT5 Strategy Backtest

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
echo  STRATEGY BACKTEST
echo ========================================================
echo.
echo MetaTrader 5 must be open and logged in.
echo Choose the instrument, timeframe, and strategy here.
echo.

".venv\Scripts\python.exe" -m gold_smc.launcher backtest %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo ERROR: Backtesting stopped with exit code %EXIT_CODE%.
    pause
)

endlocal
exit /b %EXIT_CODE%
