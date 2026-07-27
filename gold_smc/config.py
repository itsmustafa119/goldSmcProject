from pathlib import Path
import atexit
import msvcrt
import os

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIRECTORY = PROJECT_ROOT / "outputs"

SYMBOL = os.getenv("SMC_SYMBOL", "XAUUSD").strip() or "XAUUSD"
TIMEFRAME_NAME = os.getenv("SMC_TIMEFRAME", "M15").strip().upper()
TIMEFRAME_MINUTES_BY_NAME = {
    "M15": 15,
    "H1": 60,
    "H4": 240,
}

if TIMEFRAME_NAME not in TIMEFRAME_MINUTES_BY_NAME:
    raise ValueError(
        "SMC_TIMEFRAME must be one of M15, H1, or H4."
    )

TIMEFRAME_MINUTES = TIMEFRAME_MINUTES_BY_NAME[TIMEFRAME_NAME]

NUMBER_OF_CANDLES = 5000
CHART_CANDLES = 1200
SWING_LENGTH = 20
LIQUIDITY_RANGE = 0.01

SESSION_TIME_ZONE = "UTC"
SESSION_COLORS = {
    "London": "rgba(111, 201, 173, 0.07)",
    "New York": "rgba(219, 160, 121, 0.08)",
}

# Expanded zone limits for comprehensive coverage
MAX_FVG_ZONES = 64
MAX_OB_ZONES = 24
MAX_LIQUIDITY_LEVELS = 6
MAX_SWING_MARKERS = 40

# Windows process lock for preventing duplicate dashboard instances
INSTANCE_LOCK_FILE = ".gold_smc.lock"

OUTPUT_STEM = "".join(
    character.lower()
    for character in f"{SYMBOL}_{TIMEFRAME_NAME}"
    if character.isalnum() or character == "_"
)
CSV_OUTPUT_FILE = f"outputs/{OUTPUT_STEM}_indicators.csv"
HTML_OUTPUT_FILE = f"outputs/{OUTPUT_STEM}_chart.html"
PLOTLY_JS_FILE = "plotly.min.js"
MPLFINANCE_OUTPUT_FILE = f"outputs/{OUTPUT_STEM}_snapshot.png"
MPLFINANCE_CANDLES = 300

LIVE_MODE = os.getenv("SMC_LIVE_MODE", "1") not in {
    "0",
    "false",
    "False",
}
LIVE_REFRESH_SECONDS = 5
LIVE_HOST = "127.0.0.1"
LIVE_PORT = 8765

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
