from pathlib import Path

SYMBOL = "XAUUSD"
TIMEFRAME = None
TIMEFRAME_NAME = "M15"
TIMEFRAME_MINUTES = 15

NUMBER_OF_CANDLES = 5000
CHART_CANDLES = 1200
SWING_LENGTH = 20
LIQUIDITY_RANGE = 0.01

SESSION_TIME_ZONE = "UTC"
SESSION_COLORS = {
    "London": "rgba(45, 145, 255, 0.07)",
    "New York": "rgba(255, 155, 45, 0.07)",
}

MAX_FVG_ZONES = 16
MAX_OB_ZONES = 10
MAX_LIQUIDITY_LEVELS = 6
MAX_SWING_MARKERS = 40

CSV_OUTPUT_FILE = "xauusd_m15_smc_results.csv"
HTML_OUTPUT_FILE = "xauusd_m15_smc_chart.html"
PLOTLY_JS_FILE = "plotly.min.js"
MPLFINANCE_OUTPUT_FILE = "xauusd_m15_smc_snapshot.png"
MPLFINANCE_CANDLES = 300
BACKTEST_OUTPUT_FILE = "xauusd_m15_smc_backtest.html"
BACKTEST_TRADES_FILE = "xauusd_m15_smc_trades.csv"

BACKTEST_CASH = 100_000
BACKTEST_SPREAD = 0.0001
BACKTEST_MARGIN = 0.05
BACKTEST_POSITION_FRACTION = 0.10
BACKTEST_RISK_REWARD = 2.0
BACKTEST_ATR_MULTIPLIER = 1.5

LIVE_MODE = True
LIVE_REFRESH_SECONDS = 5
LIVE_HOST = "127.0.0.1"
LIVE_PORT = 8765

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def project_path(filename: str | Path) -> Path:
    """Resolve a project-relative path to the repository root."""

    path = Path(filename)
    return path if path.is_absolute() else PROJECT_ROOT / path
