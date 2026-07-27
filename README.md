![GitHub stars](https://img.shields.io/github/stars/itsmustafa119/goldSmcProject)
![GitHub license](https://img.shields.io/github/license/itsmustafa119/goldSmcProject)
![Python](https://img.shields.io/badge/python-3.x-blue)

# MT5 Smart Money Concepts

A focused MetaTrader 5 project with two workflows:

1. `run_analysis.bat` downloads a selected market and displays the interactive
   Smart Money Concepts chart.
2. `run_backtest.bat` applies a selected strategy to a selected market and
   timeframe, then displays the audited results.

Both launchers ask for the instrument and timeframe in the terminal. Supported
timeframes are M15, H1, and H4. Common instruments are listed, and a custom MT5
symbol can be entered when the broker uses another market.

## Requirements

- Windows
- MetaTrader 5 installed, open, logged in, and connected
- Python virtual environment in `.venv`

Install the dependencies with:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Market analysis

Double-click:

```text
run_analysis.bat
```

Choose the instrument and timeframe. The browser chart includes:

- candlesticks and volume;
- fair value gaps;
- order blocks;
- liquidity levels and sweeps;
- BOS and CHoCH;
- swing structure and dealing range;
- previous daily and four-hour levels;
- London and New York sessions;
- retracement, confluence, and live-session panels.

Only completed MT5 candles are analyzed. The dashboard checks for newly
completed candles and refreshes the indicators automatically.

## Strategy backtesting

Double-click:

```text
run_backtest.bat
```

Choose the instrument, execution timeframe, and one of the three retained
strategy files:

- `strategies/mtf_trend_pullback.py`
- `strategies/ict_liquidity_fvg.py`
- `strategies/confirmed_smc_pullback.py`

The report shows entries, exits, initial stop-losses, take-profits, win rate,
balance, P&L, drawdown, trade history, and the indicator values recorded at
each entry. Its chart uses the same controls and styling as the analysis
chart, but displays only the selected execution timeframe and audited trade
overlays instead of the SMC analysis layers.

Backtests use completed candles and modeled execution costs. They are
historical simulations, not guarantees of future performance.

## Generated files

Runtime files are written under `outputs/` and are intentionally excluded from
Git. The local Plotly runtime is generated automatically when missing.

## Project structure

- `gold_smc/` contains the shared application, chart, MT5, and backtest code.
- `strategies/` contains only the editable strategy definitions.
- `tests/` contains the automated regression tests.
- `run_analysis.bat` and `run_backtest.bat` are the only supported launchers.
- `outputs/`, `plotly.min.js`, lock files, caches, and bytecode are generated
  locally and can be deleted safely.

## Command-line options

The launchers accept optional arguments for repeatable runs:

```powershell
run_analysis.bat --symbol EURUSD --timeframe H1

run_backtest.bat --symbol XAUUSD --timeframe M15 --strategy strategies\ict_liquidity_fvg.py
```

Backtest-only options:

- `--use-cache` reuses the latest cached candles.
- `--no-browser` does not open the report automatically.
- `--once` writes the result and exits without keeping a local service open.

See [docs/USAGE.md](docs/USAGE.md) for troubleshooting.
