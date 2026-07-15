from pathlib import Path
import atexit
import msvcrt
import threading

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

# Expanded zone limits for comprehensive coverage
MAX_FVG_ZONES = 64
MAX_OB_ZONES = 24
MAX_LIQUIDITY_LEVELS = 6
MAX_SWING_MARKERS = 40

# Windows process lock for preventing duplicate dashboard instances
INSTANCE_LOCK_FILE = ".gold_smc.lock"

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


def release_instance_lock(lock_file) -> None:
    """Release the Windows process lock when Python exits."""

    try:
        lock_file.seek(0)
        msvcrt.locking(
            lock_file.fileno(),
            msvcrt.LK_UNLCK,
            1,
        )
    except (OSError, ValueError):
        pass

    lock_file.close()


def acquire_instance_lock():
    """Return a held lock, or None when the dashboard already runs."""

    lock_path = project_path(
        INSTANCE_LOCK_FILE
    ).resolve()
    lock_file = lock_path.open("a+b")

    if lock_path.stat().st_size == 0:
        lock_file.write(b"1")
        lock_file.flush()

    lock_file.seek(0)

    try:
        msvcrt.locking(
            lock_file.fileno(),
            msvcrt.LK_NBLCK,
            1,
        )
    except OSError:
        lock_file.close()
        return None

    atexit.register(
        release_instance_lock,
        lock_file,
    )
    return lock_file
