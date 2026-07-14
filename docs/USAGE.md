# Simple Usage Guide

This guide explains how to install, start, use, and stop the XAUUSD Smart Money Concepts Dashboard on Windows.

## 1. Prepare MetaTrader 5

1. Install and open the MetaTrader 5 desktop terminal.
2. Log in to your broker account.
3. Confirm that your broker provides a gold symbol.
4. Leave MetaTrader 5 running while the Python application is running.

The application reads market data from the locally running MT5 terminal. It does not place live orders.

## 2. Install the project

Open PowerShell and run:

```powershell
git clone https://github.com/itsmustafa119/goldSmcProject.git
cd goldSmcProject
py -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

You only need to perform these installation steps once.

## 3. Start the dashboard

Double-click:

```text
start_gold_smc.bat
```

The launcher:

1. Connects to MetaTrader 5.
2. Downloads completed candles.
3. Calculates the SMC indicators.
4. Creates the Plotly and mplfinance charts.
5. Runs the historical strategy simulation.
6. Starts the local dashboard server.
7. Opens the dashboard in your browser.

Keep the command window open. The default browser address is:

```text
http://127.0.0.1:8765/
```

## 4. Use the chart

- Hover over candles, zones, and markers to see their values.
- Drag horizontally to move through time.
- Use the mouse wheel or Plotly toolbar to zoom.
- Use `1D`, `3D`, `1W`, or `All` to change the visible period.
- Use `Y+` and `Y-` for manual vertical scaling.
- Use `Y Auto` to fit the visible candle highs and lows.
- Open `Indicators` to show or hide FVGs, order blocks, liquidity, structure, sessions, levels, and labels.
- Open `Summary` to see current values and a short definition of each indicator.
- Open `Backtest` to inspect performance, drawdown, trades, and entry/exit markers.

The chart begins with the latest three calendar days and hides annotation text to reduce clutter. The indicator shapes remain visible, and labels can be enabled from the Indicators panel.

## 5. Live updates

The application checks MT5 every five seconds. It refreshes all indicators, charts, and the backtest only when it finds a newly completed M15 candle.

The live price badge can update between candle closes, but the SMC calculations use completed candles to reduce intrabar repainting.

## 6. Stop the application

Return to the launcher window and press:

```text
Ctrl+C
```

Closing only the browser tab does not stop the Python process.

## Changing the symbol

Broker symbol names are not always identical. If your broker uses `XAUUSDm`, `XAUUSD.a`, `GOLD`, or another name:

1. Open `analyze_gold_mt5.py` in a text editor.
2. Find the settings section near the top.
3. Change:

   ```python
   SYMBOL = "XAUUSD"
   ```

   to the exact symbol shown in MetaTrader 5, for example:

   ```python
   SYMBOL = "XAUUSDm"
   ```

4. Save the file and restart `start_gold_smc.bat`.

## Creating one static report

The normal setting is:

```python
LIVE_MODE = True
```

To create the files once and exit instead of running the local live server, change it to:

```python
LIVE_MODE = False
```

The application will generate the files and open the standalone Plotly HTML report.

## Where the results are saved

All generated files are written to the project folder:

- `xauusd_m15_smc_results.csv` contains candles and calculated indicator values.
- `xauusd_m15_smc_chart.html` is the main standalone chart.
- `xauusd_m15_smc_snapshot.png` is the mplfinance image.
- `xauusd_m15_smc_backtest.html` is the interactive strategy report.
- `xauusd_m15_smc_trades.csv` contains the simulated trades.

## Common problems

### The virtual environment was not found

Run the installation commands from section 2. The `.venv` folder must be inside the project folder.

### MetaTrader 5 could not be initialized

- Start the MT5 desktop terminal first.
- Make sure it is logged in and connected.
- Run MT5 and the launcher from the same Windows user account.
- Close and reopen MT5 if its Python connection is stuck.

### The gold symbol was not found

Check the exact broker symbol in MT5 Market Watch and update `SYMBOL` as shown above.

### Port 8765 is already in use

The application tries the next available ports automatically. Use the URL printed in the launcher window.

### The Backtest button shows no report

Reinstall the dependencies and restart the application:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Also check the launcher window for a `Backtest warning` message.

## Safety note

The dashboard is an analysis and research tool. The included backtest is a simplified historical simulation and does not represent live broker execution or future profitability.
