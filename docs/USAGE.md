# Usage and troubleshooting

## First run

1. Open MetaTrader 5 and log in.
2. Confirm that the desired instrument is available in Market Watch.
3. Double-click `run_analysis.bat` or `run_backtest.bat`.
4. Select the instrument and then M15, H1, or H4.

The launcher resolves common broker suffixes automatically. For example,
entering `XAUUSD` can resolve to a broker symbol such as `XAUUSD.a`.

## The analysis launcher

`run_analysis.bat` opens the indicator dashboard, normally on
`http://127.0.0.1:8765/`. Keep its terminal open while using the live page.
Press Ctrl+C to stop it.

Generated chart, indicator CSV, and snapshot files are stored in `outputs/`
using the selected symbol and timeframe in their filenames.

## The backtest launcher

`run_backtest.bat` asks for a strategy after the market selection. The report
normally opens on `http://127.0.0.1:8767/`. It uses fresh MT5 candles unless
`--use-cache` is supplied.

Starting balance is $500, default lot size is 0.01, and leverage is 500:1.
The broker's contract size is used for fresh downloads. Win rate is the
percentage of completed trades with positive P&L; balance and total profit are
reported separately.

The backtest chart uses the same interaction model as the analysis chart.
The selected timeframe is the execution timeframe. Its overlays are limited
to numbered entries and exits, trade paths, initial stop-losses, and initial
take-profits. Use **Results** to jump to the balance summary and complete
audited trade table.

## Common problems

### MetaTrader 5 cannot connect

Keep MT5 open, logged in, and connected. Confirm the Python MT5 package can
access the same terminal installation.

### Symbol not found

Choose the custom-symbol option and enter the broker's exact Market Watch
name. Broker prefixes and suffixes vary.

### Port already in use

Stop an older analysis or backtest terminal. The services also try several
nearby ports and print the final URL in the terminal.

### Chart takes time to open

The first run calculates indicators and creates the local Plotly JavaScript
file. Later runs reuse that file. H1 and H4 downloads usually contain fewer
candles and therefore load faster than M15.

### Cached data is stale

Run backtesting without `--use-cache` to download fresh completed candles.

## Files that should be edited

- `gold_smc/` contains the application.
- `strategies/` contains the three editable strategy rules.
- `run_analysis.bat` and `run_backtest.bat` are the only launchers.

Files under `outputs/` are generated and can be deleted at any time.
The root `plotly.min.js` file, `.gold_smc.lock`, and Python cache directories
are also generated automatically.
