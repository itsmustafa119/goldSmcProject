from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import json
import threading
import time
import webbrowser

import MetaTrader5 as mt5
import matplotlib

matplotlib.use("Agg")

import pandas as pd
import plotly.graph_objects as go
import mplfinance as mpf
from plotly.offline import get_plotlyjs
from matplotlib import pyplot as plt
from matplotlib.colors import to_rgba
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from smartmoneyconcepts import smc
from smc_backtest import run_smc_backtest


# =========================================================
# SETTINGS
# =========================================================

SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M15
TIMEFRAME_NAME = "M15"
TIMEFRAME_MINUTES = 15

# Historical candles downloaded from MetaTrader 5.
NUMBER_OF_CANDLES = 5000

# Candles initially displayed on the chart.
CHART_CANDLES = 1200

SWING_LENGTH = 20
LIQUIDITY_RANGE = 0.01

# MT5 timestamps are Unix timestamps and are treated as UTC.
SESSION_TIME_ZONE = "UTC"
SESSION_COLORS = {
    "London": "rgba(45, 145, 255, 0.07)",
    "New York": "rgba(255, 155, 45, 0.07)",
}

# Limit visible active zones to keep the chart clean.
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

# Baseline backtest assumptions. These are intentionally visible in the
# dashboard because historical results depend heavily on execution costs,
# leverage, sizing, and exit rules.
BACKTEST_CASH = 100_000
BACKTEST_SPREAD = 0.0001
BACKTEST_MARGIN = 0.05
BACKTEST_POSITION_FRACTION = 0.10
BACKTEST_RISK_REWARD = 2.0
BACKTEST_ATR_MULTIPLIER = 1.5

# Live dashboard settings. Set LIVE_MODE to False to create a
# standalone HTML file once and exit as before.
LIVE_MODE = True
LIVE_REFRESH_SECONDS = 5
LIVE_HOST = "127.0.0.1"
LIVE_PORT = 8765

LIVE_STATE = {
    "version": 0,
    "last_candle_time": None,
    "last_price": None,
    "updated_at": None,
    "error": None,
}
LIVE_STATE_LOCK = threading.Lock()


# =========================================================
# FIND GOLD SYMBOLS
# =========================================================

def find_gold_symbols() -> list[str]:
    """Find possible gold symbols in MetaTrader 5."""

    symbols = mt5.symbols_get()

    if symbols is None:
        return []

    return [
        item.name
        for item in symbols
        if "XAU" in item.name.upper()
        or "GOLD" in item.name.upper()
    ]


# =========================================================
# DOWNLOAD MT5 DATA
# =========================================================

def get_mt5_candles(
    symbol: str,
    timeframe: int,
    candle_count: int,
) -> pd.DataFrame:
    """Download completed candles from MetaTrader 5."""

    symbol_info = mt5.symbol_info(symbol)

    if symbol_info is None:
        raise ValueError(
            f"Symbol '{symbol}' was not found.\n"
            f"Possible gold symbols: {find_gold_symbols()}"
        )

    if not symbol_info.visible:
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(
                f"Could not enable {symbol} in Market Watch."
            )

    # Candle 0 is unfinished, so start from candle 1.
    rates = mt5.copy_rates_from_pos(
        symbol,
        timeframe,
        1,
        candle_count,
    )

    if rates is None or len(rates) == 0:
        raise RuntimeError(
            f"No candle data received for {symbol}.\n"
            f"MetaTrader error: {mt5.last_error()}"
        )

    data = pd.DataFrame(rates)

    data["time"] = pd.to_datetime(
        data["time"],
        unit="s",
    )

    data = data.rename(
        columns={
            "tick_volume": "volume",
        }
    )

    required_columns = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in data.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Missing candle columns: {missing_columns}"
        )

    return (
        data[required_columns]
        .sort_values("time")
        .reset_index(drop=True)
    )


# =========================================================
# CALCULATE SMC INDICATORS
# =========================================================

def calculate_structure_map(
    data: pd.DataFrame,
    swings: pd.DataFrame,
) -> pd.DataFrame:
    """Classify confirmed swings and the current dealing range."""

    structure = pd.DataFrame(
        index=data.index,
        data={
            "Support": float("nan"),
            "Resistance": float("nan"),
            "SwingLabel": pd.Series(
                pd.NA,
                index=data.index,
                dtype="object",
            ),
            "State": pd.Series(
                "Insufficient structure",
                index=data.index,
                dtype="object",
            ),
            "RangeHigh": float("nan"),
            "RangeLow": float("nan"),
            "Equilibrium": float("nan"),
            "RangeStartIndex": float("nan"),
            "Zone": pd.Series(
                "Unavailable",
                index=data.index,
                dtype="object",
            ),
        },
    )

    last_high = None
    previous_high = None
    last_high_index = None
    last_low = None
    previous_low = None
    last_low_index = None
    state = "Insufficient structure"

    for index in data.index:
        direction = swings.at[index, "HighLow"]
        level = swings.at[index, "Level"]

        if pd.notna(direction) and pd.notna(level):
            level = float(level)

            if float(direction) == 1:
                structure.at[index, "Resistance"] = level
                structure.at[index, "SwingLabel"] = (
                    "SH"
                    if last_high is None
                    else "HH"
                    if level > last_high
                    else "LH"
                    if level < last_high
                    else "EH"
                )
                previous_high = last_high
                last_high = level
                last_high_index = int(index)

            elif float(direction) == -1:
                structure.at[index, "Support"] = level
                structure.at[index, "SwingLabel"] = (
                    "SL"
                    if last_low is None
                    else "HL"
                    if level > last_low
                    else "LL"
                    if level < last_low
                    else "EL"
                )
                previous_low = last_low
                last_low = level
                last_low_index = int(index)

        if (
            previous_high is not None
            and previous_low is not None
            and last_high is not None
            and last_low is not None
        ):
            higher_high = last_high > previous_high
            higher_low = last_low > previous_low
            lower_high = last_high < previous_high
            lower_low = last_low < previous_low

            if higher_high and higher_low:
                state = "Uptrend"
            elif lower_high and lower_low:
                state = "Downtrend"
            else:
                state = "Transition / range"

        structure.at[index, "State"] = state

        if (
            last_high is None
            or last_low is None
            or last_high_index is None
            or last_low_index is None
        ):
            continue

        range_high = max(last_high, last_low)
        range_low = min(last_high, last_low)
        equilibrium = (range_high + range_low) / 2
        range_size = range_high - range_low
        close = float(data.at[index, "close"])

        structure.at[index, "RangeHigh"] = range_high
        structure.at[index, "RangeLow"] = range_low
        structure.at[index, "Equilibrium"] = equilibrium
        structure.at[index, "RangeStartIndex"] = min(
            last_high_index,
            last_low_index,
        )

        if close > range_high:
            zone = "Above dealing range"
        elif close < range_low:
            zone = "Below dealing range"
        elif range_size > 0 and abs(close - equilibrium) <= range_size * 0.02:
            zone = "Equilibrium"
        elif close < equilibrium:
            zone = "Discount"
        else:
            zone = "Premium"

        structure.at[index, "Zone"] = zone

    return structure


def calculate_smc_indicators(
    data: pd.DataFrame,
) -> pd.DataFrame:
    """Calculate ICT/SMC indicators."""

    swings = smc.swing_highs_lows(
        data,
        swing_length=SWING_LENGTH,
    )

    fair_value_gaps = smc.fvg(
        data,
        join_consecutive=False,
    )

    structure_map = calculate_structure_map(
        data,
        swings,
    )

    market_structure = smc.bos_choch(
        data,
        swings,
        close_break=True,
    )

    liquidity = smc.liquidity(
        data,
        swings,
        range_percent=LIQUIDITY_RANGE,
    )

    order_blocks = smc.ob(
        data,
        swings,
        close_mitigation=False,
    )

    # previous_high_low requires time as the index.
    indexed_data = data.set_index("time")[
        [
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    ]

    previous_levels = smc.previous_high_low(
        indexed_data,
        time_frame="1D",
    ).reset_index(drop=True)

    four_hour_levels = smc.previous_high_low(
        indexed_data,
        time_frame="4h",
    ).reset_index(drop=True)

    retracements = smc.retracements(
        data,
        swings,
    )

    session_frames = []

    for session_name in SESSION_COLORS:
        session_prefix = session_name.replace(" ", "")

        session_data = smc.sessions(
            indexed_data.copy(),
            session=session_name,
            time_zone=SESSION_TIME_ZONE,
        ).reset_index(drop=True)

        session_frames.append(
            session_data.add_prefix(
                f"Session_{session_prefix}_"
            )
        )

    results = pd.concat(
        [
            data.reset_index(drop=True),

            swings
            .reset_index(drop=True)
            .add_prefix("Swing_"),

            structure_map
            .reset_index(drop=True)
            .add_prefix("Trend_"),

            fair_value_gaps
            .reset_index(drop=True)
            .add_prefix("FVG_"),

            market_structure
            .reset_index(drop=True)
            .add_prefix("Structure_"),

            liquidity
            .reset_index(drop=True)
            .add_prefix("Liquidity_"),

            order_blocks
            .reset_index(drop=True)
            .add_prefix("OB_"),

            previous_levels
            .add_prefix("Daily_"),

            four_hour_levels
            .add_prefix("FourHour_"),

            retracements
            .reset_index(drop=True)
            .add_prefix("Retracement_"),

            *session_frames,
        ],
        axis=1,
    )

    validate_indicator_results(
        results
    )

    return results


def validate_indicator_results(
    results: pd.DataFrame,
) -> None:
    """Fail clearly if the upstream SMC API shape changes."""

    expected_columns = {
        "Swing_HighLow",
        "Swing_Level",
        "Trend_Support",
        "Trend_Resistance",
        "Trend_SwingLabel",
        "Trend_State",
        "Trend_RangeHigh",
        "Trend_RangeLow",
        "Trend_Equilibrium",
        "Trend_RangeStartIndex",
        "Trend_Zone",
        "FVG_FVG",
        "FVG_Top",
        "FVG_Bottom",
        "FVG_MitigatedIndex",
        "Structure_BOS",
        "Structure_CHOCH",
        "Structure_Level",
        "Structure_BrokenIndex",
        "Liquidity_Liquidity",
        "Liquidity_Level",
        "Liquidity_End",
        "Liquidity_Swept",
        "OB_OB",
        "OB_Top",
        "OB_Bottom",
        "OB_OBVolume",
        "OB_MitigatedIndex",
        "OB_Percentage",
        "Daily_PreviousHigh",
        "Daily_PreviousLow",
        "Daily_BrokenHigh",
        "Daily_BrokenLow",
        "FourHour_PreviousHigh",
        "FourHour_PreviousLow",
        "FourHour_BrokenHigh",
        "FourHour_BrokenLow",
        "Retracement_Direction",
        "Retracement_CurrentRetracement%",
        "Retracement_DeepestRetracement%",
        "Session_London_Active",
        "Session_London_High",
        "Session_London_Low",
        "Session_NewYork_Active",
        "Session_NewYork_High",
        "Session_NewYork_Low",
    }

    missing_columns = sorted(
        expected_columns - set(results.columns)
    )

    if missing_columns:
        raise ValueError(
            "The smartmoneyconcepts output schema changed. "
            f"Missing columns: {missing_columns}"
        )

    direction_columns = [
        "Swing_HighLow",
        "FVG_FVG",
        "Structure_BOS",
        "Structure_CHOCH",
        "Liquidity_Liquidity",
        "OB_OB",
        "Retracement_Direction",
    ]

    for column in direction_columns:
        unexpected = set(
            results[column]
            .dropna()
            .astype(float)
            .unique()
        ) - {-1.0, 0.0, 1.0}

        if unexpected:
            raise ValueError(
                f"Unexpected values in {column}: "
                f"{sorted(unexpected)}"
            )

    for session_column in [
        "Session_London_Active",
        "Session_NewYork_Active",
    ]:
        unexpected = set(
            results[session_column]
            .dropna()
            .astype(int)
            .unique()
        ) - {0, 1}

        if unexpected:
            raise ValueError(
                f"Unexpected values in {session_column}: "
                f"{sorted(unexpected)}"
            )


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def indicator_is_active(value) -> bool:
    """
    NaN or zero means the zone has not yet been
    mitigated or swept.
    """

    if pd.isna(value):
        return True

    try:
        return int(value) <= 0
    except (TypeError, ValueError):
        return True


def get_index_time(
    data: pd.DataFrame,
    index_value,
    default_time,
):
    """Convert an indicator candle index to candle time."""

    if pd.isna(index_value):
        return default_time

    try:
        candle_index = int(index_value)
    except (TypeError, ValueError):
        return default_time

    if 0 <= candle_index < len(data):
        return data.iloc[candle_index]["time"]

    return default_time


def latest_value(
    data: pd.DataFrame,
    column: str,
):
    """Return the latest non-empty value from a column."""

    if column not in data.columns:
        return None

    values = data[column].dropna()

    if values.empty:
        return None

    return values.iloc[-1]


def nearest_structure_level(
    data: pd.DataFrame,
    column: str,
    current_price: float,
    *,
    above_price: bool,
):
    """Return the closest confirmed structure level on one side of price."""

    if column not in data.columns:
        return None

    levels = pd.to_numeric(
        data[column],
        errors="coerce",
    ).dropna()

    levels = (
        levels[levels >= current_price]
        if above_price
        else levels[levels <= current_price]
    )

    if levels.empty:
        return None

    distances = (levels - current_price).abs()
    return float(levels.loc[distances.idxmin()])


def retracement_status(
    data: pd.DataFrame,
) -> str:
    """Return a compact description of the latest retracement."""

    required_columns = {
        "Retracement_Direction",
        "Retracement_CurrentRetracement%",
        "Retracement_DeepestRetracement%",
    }

    if not required_columns.issubset(data.columns):
        return "Retracement unavailable"

    latest = data.iloc[-1]

    direction_value = latest[
        "Retracement_Direction"
    ]

    direction = (
        "Bullish leg"
        if direction_value == 1
        else "Bearish leg"
        if direction_value == -1
        else "Neutral"
    )

    current = float(
        latest[
            "Retracement_CurrentRetracement%"
        ]
    )

    deepest = float(
        latest[
            "Retracement_DeepestRetracement%"
        ]
    )

    return (
        f"{direction} · Retracement {current:.1f}% "
        f"· Deepest {deepest:.1f}%"
    )


def build_backtest_dashboard(
    summary: dict | None,
) -> str:
    """Build backtest metrics, assumptions, and recent trades."""

    if not summary:
        return """
<section class="backtest-dashboard" aria-labelledby="backtest-title">
    <header class="reference-header">
        <p class="eyebrow">Historical strategy simulation</p>
        <h2 id="backtest-title">Backtest unavailable</h2>
        <p>The backtest did not produce a result during this refresh.</p>
    </header>
</section>
"""

    def metric(
        key: str,
        *,
        suffix: str = "",
        decimals: int = 2,
        money: bool = False,
    ) -> str:
        value = summary.get(key)

        if value is None or pd.isna(value):
            return "—"

        if money:
            return f"${float(value):,.2f}"

        return f"{float(value):,.{decimals}f}{suffix}"

    trade_rows = []

    for trade in summary.get("recent_trades", []):
        outcome_class = (
            "trade-win"
            if trade["outcome"] == "Win"
            else "trade-loss"
            if trade["outcome"] == "Loss"
            else ""
        )
        trade_rows.append(
            "<tr>"
            f"<td>{trade['direction']}</td>"
            f"<td>{trade['entry_time']}</td>"
            f"<td>{trade['exit_time']}</td>"
            f"<td>{trade['entry_price']:,.2f}</td>"
            f"<td>{trade['exit_price']:,.2f}</td>"
            f"<td class=\"{outcome_class}\">{trade['pnl']:,.2f}</td>"
            f"<td class=\"{outcome_class}\">{trade['return_pct']:,.2f}%</td>"
            "</tr>"
        )

    table_body = (
        "".join(trade_rows)
        if trade_rows
        else "<tr><td colspan=\"7\">No completed trades for this rule set.</td></tr>"
    )

    leverage = (
        1 / float(summary["margin"])
        if float(summary.get("margin", 0)) > 0
        else 1
    )

    return f"""
<section class="backtest-dashboard" aria-labelledby="backtest-title">
    <header class="dashboard-header">
        <div>
            <p class="eyebrow">Historical strategy simulation</p>
            <h2 id="backtest-title">SMC pullback backtest</h2>
            <p>{summary['start']} to {summary['end']} · Confirmed trend + structure bias + discount/premium pullback · ATR stop · {summary['risk_reward']:.1f}R target.</p>
        </div>
        <div class="backtest-links">
            <a class="back-to-chart" href="{BACKTEST_OUTPUT_FILE}" target="_blank" rel="noopener">Interactive results</a>
            <a class="back-to-chart" href="{BACKTEST_TRADES_FILE}" download>Trade CSV</a>
        </div>
    </header>

    <div class="metric-grid backtest-metrics">
        <article class="metric-card metric-primary">
            <span>Strategy return</span>
            <strong>{metric('return_pct', suffix='%')}</strong>
            <small>Buy &amp; hold {metric('buy_hold_return_pct', suffix='%')}</small>
        </article>
        <article class="metric-card">
            <span>Completed trades</span>
            <strong>{summary['trades']}</strong>
            <small>{summary['long_setups']} long · {summary['short_setups']} short setups</small>
        </article>
        <article class="metric-card">
            <span>Win rate</span>
            <strong>{metric('win_rate_pct', suffix='%')}</strong>
            <small>Expectancy {metric('expectancy_pct', suffix='%')}</small>
        </article>
        <article class="metric-card">
            <span>Maximum drawdown</span>
            <strong>{metric('max_drawdown_pct', suffix='%')}</strong>
            <small>Profit factor {metric('profit_factor')}</small>
        </article>
        <article class="metric-card">
            <span>Final equity</span>
            <strong>{metric('equity_final', money=True)}</strong>
            <small>Starting cash ${summary['cash']:,.0f}</small>
        </article>
        <article class="metric-card metric-wide">
            <span>Execution assumptions</span>
            <strong>{summary['position_fraction'] * 100:.0f}% liquidity per trade · {leverage:.0f}:1 leverage</strong>
            <small>{summary['spread'] * 10_000:.1f} bp spread · close-bar fills · {summary['atr_multiplier']:.1f} ATR minimum stop · pivots delayed {summary['swing_confirmation_bars']} candles</small>
        </article>
    </div>

    <div class="trade-table-wrap">
        <table class="trade-table">
            <caption>Most recent completed trades</caption>
            <thead>
                <tr><th>Side</th><th>Entry</th><th>Exit</th><th>Entry price</th><th>Exit price</th><th>P&amp;L</th><th>Return</th></tr>
            </thead>
            <tbody>{table_body}</tbody>
        </table>
    </div>

    <p class="analysis-disclaimer">Hypothetical historical simulation only—not a forecast or trading recommendation. Results omit slippage beyond the configured spread, financing, broker-specific contract sizing, news restrictions, and execution failures. Parameters were not optimized on this sample.</p>
</section>
"""


def build_analysis_dashboard(
    data: pd.DataFrame,
    backtest_summary: dict | None = None,
) -> str:
    """Build summary metrics and an indicator reference section."""

    latest = data.iloc[-1]

    def price_value(column: str) -> str:
        value = latest_value(data, column)

        if value is None or pd.isna(value):
            return "—"

        return f"{float(value):,.2f}"

    def active_count(
        signal_column: str,
        completion_column: str,
    ) -> int:
        if signal_column not in data.columns:
            return 0

        signals = data[
            data[signal_column].fillna(0).ne(0)
        ]

        if completion_column not in signals.columns:
            return len(signals)

        return int(
            signals[completion_column]
            .apply(indicator_is_active)
            .sum()
        )

    latest_structure = "No confirmed structure break"

    if {
        "Structure_BOS",
        "Structure_CHOCH",
        "Structure_BrokenIndex",
    }.issubset(data.columns):
        structure_rows = data[
            (
                data["Structure_BOS"].fillna(0).ne(0)
                | data["Structure_CHOCH"].fillna(0).ne(0)
            )
            & data["Structure_BrokenIndex"].notna()
        ].copy()

        if not structure_rows.empty:
            structure_rows["_break"] = pd.to_numeric(
                structure_rows["Structure_BrokenIndex"],
                errors="coerce",
            )

            structure_row = structure_rows.sort_values(
                "_break"
            ).iloc[-1]

            if pd.notna(structure_row["Structure_BOS"]):
                structure_name = "BOS"
                direction_value = structure_row["Structure_BOS"]
            else:
                structure_name = "CHoCH"
                direction_value = structure_row["Structure_CHOCH"]

            direction_name = (
                "Bullish"
                if direction_value == 1
                else "Bearish"
            )

            break_time = get_index_time(
                data,
                structure_row["Structure_BrokenIndex"],
                structure_row["time"],
            )

            latest_structure = (
                f"{direction_name} {structure_name} · "
                f"{float(structure_row['Structure_Level']):,.2f} · "
                f"{break_time}"
            )

    active_sessions = []

    for session_name in SESSION_COLORS:
        session_prefix = session_name.replace(" ", "")
        column = f"Session_{session_prefix}_Active"

        if column in data.columns and latest[column] == 1:
            active_sessions.append(session_name)

    session_text = (
        ", ".join(active_sessions)
        if active_sessions
        else "No tracked session active"
    )

    fvg_count = active_count(
        "FVG_FVG",
        "FVG_MitigatedIndex",
    )
    ob_count = active_count(
        "OB_OB",
        "OB_MitigatedIndex",
    )
    liquidity_count = active_count(
        "Liquidity_Liquidity",
        "Liquidity_Swept",
    )

    retracement_text = retracement_status(data)
    current_price = float(latest["close"])
    trend_state = str(
        latest.get("Trend_State", "Unavailable")
    )
    dealing_zone = str(
        latest.get("Trend_Zone", "Unavailable")
    )
    nearest_support = nearest_structure_level(
        data,
        "Trend_Support",
        current_price,
        above_price=False,
    )
    nearest_resistance = nearest_structure_level(
        data,
        "Trend_Resistance",
        current_price,
        above_price=True,
    )
    support_text = (
        f"{nearest_support:,.2f}"
        if nearest_support is not None
        else "—"
    )
    resistance_text = (
        f"{nearest_resistance:,.2f}"
        if nearest_resistance is not None
        else "—"
    )

    backtest_html = build_backtest_dashboard(
        backtest_summary
    )

    return f"""
<section id="chart-summary" class="analysis-dashboard" aria-labelledby="summary-title">
    <header class="dashboard-header">
        <div>
            <p class="eyebrow">XAUUSD {TIMEFRAME_NAME} analysis</p>
            <h2 id="summary-title">Market summary</h2>
            <p>Latest completed candle: {latest['time']} · Indicators use completed candles to avoid intrabar repainting.</p>
        </div>
        <a class="back-to-chart" href="#chart-shell">Back to chart</a>
    </header>

    <div class="metric-grid">
        <article class="metric-card metric-primary">
            <span>Live / latest price</span>
            <strong id="live-current-price">{float(latest['close']):,.2f}</strong>
            <small>O {float(latest['open']):,.2f} · H {float(latest['high']):,.2f} · L {float(latest['low']):,.2f} · C {float(latest['close']):,.2f}</small>
        </article>
        <article class="metric-card">
            <span>Previous day</span>
            <strong>H {price_value('Daily_PreviousHigh')}</strong>
            <small>L {price_value('Daily_PreviousLow')}</small>
        </article>
        <article class="metric-card">
            <span>Previous 4H</span>
            <strong>H {price_value('FourHour_PreviousHigh')}</strong>
            <small>L {price_value('FourHour_PreviousLow')}</small>
        </article>
        <article class="metric-card">
            <span>Active zones</span>
            <strong>{fvg_count} FVG · {ob_count} OB</strong>
            <small>{liquidity_count} unswept liquidity pools</small>
        </article>
        <article class="metric-card">
            <span>Swing trend</span>
            <strong>{trend_state}</strong>
            <small>HH/HL confirms uptrend · LH/LL confirms downtrend</small>
        </article>
        <article class="metric-card">
            <span>Dealing range</span>
            <strong>{dealing_zone}</strong>
            <small>Support {support_text} · Resistance {resistance_text}</small>
        </article>
        <article class="metric-card metric-wide">
            <span>Latest confirmed structure</span>
            <strong>{latest_structure}</strong>
        </article>
        <article class="metric-card metric-wide">
            <span>Retracement / session</span>
            <strong>{retracement_text}</strong>
            <small>{session_text}</small>
        </article>
    </div>

    <header class="reference-header">
        <p class="eyebrow">Indicator reference</p>
        <h2>Definitions and practical use</h2>
        <p>These describe what the library calculates. They are context tools, not standalone trade signals.</p>
    </header>

    <div class="definition-grid">
        <article class="definition-card fvg-definition">
            <h3>Fair Value Gap <span>FVG</span></h3>
            <p><strong>Definition:</strong> A three-candle imbalance where price leaves a gap between candle one and candle three.</p>
            <p><strong>Use:</strong> Watch for price revisiting the zone, reacting from it, or fully mitigating it. Separate rectangles represent separate gaps.</p>
        </article>
        <article class="definition-card ob-definition">
            <h3>Order Block <span>OB</span></h3>
            <p><strong>Definition:</strong> The source price range preceding displacement through a swing level.</p>
            <p><strong>Use:</strong> Treat it as a potential reaction area. Volume and percentage describe the library's relative OB activity, not win probability.</p>
        </article>
        <article class="definition-card liquidity-definition">
            <h3>Liquidity</h3>
            <p><strong>Definition:</strong> Multiple swing highs or lows grouped within the configured price tolerance.</p>
            <p><strong>Use:</strong> These areas may contain clustered stops. A sweep shows price trading through the pool; confirmation is still required.</p>
        </article>
        <article class="definition-card structure-definition">
            <h3>BOS and CHoCH</h3>
            <p><strong>BOS:</strong> A confirmed structural break commonly interpreted as continuation.</p>
            <p><strong>CHoCH:</strong> A confirmed change in swing sequence that can warn of a possible regime shift or reversal.</p>
        </article>
        <article class="definition-card structure-map-definition">
            <h3>Structure Map <span>HH / HL / LH / LL</span></h3>
            <p><strong>Definition:</strong> Confirmed swing highs and lows are compared with the previous pivot of the same type to classify trend structure and nearby support or resistance.</p>
            <p><strong>Use:</strong> HH plus HL supports an uptrend; LH plus LL supports a downtrend. Mixed pairs indicate transition or range conditions.</p>
        </article>
        <article class="definition-card dealing-range-definition">
            <h3>Premium and Discount</h3>
            <p><strong>Definition:</strong> The midpoint of the latest confirmed high-low dealing range is equilibrium. Price below it is discount; price above it is premium.</p>
            <p><strong>Use:</strong> Combine the zone with directional structure and confirmation. A discount reading alone is not a buy signal, and premium alone is not a sell signal.</p>
        </article>
        <article class="definition-card swing-definition">
            <h3>Swing Highs and Lows</h3>
            <p><strong>Definition:</strong> Confirmed pivots that are highest or lowest across the configured candles before and after.</p>
            <p><strong>Use:</strong> They anchor structure, liquidity, order blocks, and retracement calculations. They confirm only after future candles exist.</p>
        </article>
        <article class="definition-card levels-definition">
            <h3>Previous Highs and Lows</h3>
            <p><strong>Definition:</strong> High and low of the preceding 4H or daily period, with broken-state tracking.</p>
            <p><strong>Use:</strong> Common reference levels for targets, liquidity, support/resistance, and intraday bias.</p>
        </article>
        <article class="definition-card sessions-definition">
            <h3>Trading Sessions</h3>
            <p><strong>Definition:</strong> Session membership plus the developing session high and low in UTC.</p>
            <p><strong>Use:</strong> Compare volatility, range expansion, and reactions during London and New York hours.</p>
        </article>
        <article class="definition-card retracement-definition">
            <h3>Retracements</h3>
            <p><strong>Definition:</strong> Current and deepest percentage retracement between confirmed swing extremes.</p>
            <p><strong>Use:</strong> Measure pullback depth and compare it with structure. Values can exceed 100% after extension beyond the reference swing.</p>
        </article>
    </div>

    <p class="analysis-disclaimer">Educational analysis only. SMC labels are algorithmic interpretations and should not be used as the sole basis for a trade.</p>
</section>
{backtest_html}
"""


def add_session_regions(
    fig: go.Figure,
    data: pd.DataFrame,
    session_name: str,
    fill_color: str,
) -> None:
    """Shade each contiguous active trading session."""

    session_prefix = session_name.replace(" ", "")
    active_column = f"Session_{session_prefix}_Active"
    high_column = f"Session_{session_prefix}_High"
    low_column = f"Session_{session_prefix}_Low"

    required_columns = {
        active_column,
        high_column,
        low_column,
    }

    if not required_columns.issubset(data.columns):
        return

    active = data[active_column].fillna(0).eq(1)

    if not active.any():
        return

    groups = active.ne(
        active.shift(fill_value=False)
    ).cumsum()

    show_legend = True

    for _, session_rows in data[active].groupby(
        groups[active]
    ):
        start_time = session_rows["time"].iloc[0]
        end_time = (
            session_rows["time"].iloc[-1]
            + pd.Timedelta(minutes=TIMEFRAME_MINUTES)
        )

        session_high = float(
            session_rows[high_column].max()
        )

        positive_lows = session_rows.loc[
            session_rows[low_column] > 0,
            low_column,
        ]

        if positive_lows.empty:
            continue

        session_low = float(
            positive_lows.min()
        )

        fig.add_shape(
            type="rect",
            xref="x",
            yref="y",
            x0=start_time,
            x1=end_time,
            y0=session_low,
            y1=session_high,
            fillcolor=fill_color,
            line={"width": 0},
            layer="below",
            name=f"{session_name} Session",
            legendgroup=f"session_{session_prefix}",
            showlegend=show_legend,
        )

        show_legend = False


def add_swing_markers(
    fig: go.Figure,
    data: pd.DataFrame,
) -> None:
    """Draw a limited number of recent swing highs and lows."""

    required_columns = {
        "Swing_HighLow",
        "Swing_Level",
    }

    if not required_columns.issubset(data.columns):
        return

    swings = data[
        data["Swing_HighLow"].fillna(0).ne(0)
        & data["Swing_Level"].notna()
    ].tail(MAX_SWING_MARKERS)

    swing_rows = list(
        swings.iterrows()
    )

    for position in range(
        len(swing_rows) - 1
    ):
        _, start = swing_rows[position]
        _, end = swing_rows[position + 1]

        line_color = (
            "rgba(45, 220, 120, 0.48)"
            if start["Swing_HighLow"] == -1
            else "rgba(255, 80, 95, 0.48)"
        )

        fig.add_trace(
            go.Scatter(
                x=[start["time"], end["time"]],
                y=[start["Swing_Level"], end["Swing_Level"]],
                mode="lines",
                line={
                    "color": line_color,
                    "width": 1.6,
                },
                name="Swing Structure",
                legendgroup="swings",
                showlegend=False,
                hoverinfo="skip",
            )
        )

    definitions = [
        (1, "Swing High", "#69d7ff", "triangle-down-open"),
        (-1, "Swing Low", "#ff77d4", "triangle-up-open"),
    ]

    for direction, label, color, symbol in definitions:
        selected = swings[
            swings["Swing_HighLow"] == direction
        ]

        fig.add_trace(
            go.Scatter(
                x=selected["time"],
                y=selected["Swing_Level"],
                mode="markers",
                marker={
                    "symbol": symbol,
                    "size": 10,
                    "color": color,
                    "line": {
                        "color": color,
                        "width": 1.7,
                    },
                },
                name=label,
                legendgroup="swings",
                hovertemplate=(
                    f"<b>{label}</b><br>"
                    "Level: %{y:.2f}<br>"
                    "Time: %{x}<extra></extra>"
                ),
            )
        )


def add_structure_map(
    fig: go.Figure,
    data: pd.DataFrame,
    first_time,
    last_time,
    current_price: float,
) -> None:
    """Draw trend labels, nearby S/R, and the latest dealing range."""

    required_columns = {
        "Trend_Support",
        "Trend_Resistance",
        "Trend_SwingLabel",
        "Trend_RangeHigh",
        "Trend_RangeLow",
        "Trend_Equilibrium",
        "Trend_RangeStartIndex",
    }

    if not required_columns.issubset(data.columns):
        return

    latest = data.iloc[-1]
    range_high = latest["Trend_RangeHigh"]
    range_low = latest["Trend_RangeLow"]
    equilibrium = latest["Trend_Equilibrium"]
    range_start_index = latest["Trend_RangeStartIndex"]

    if all(
        pd.notna(value)
        for value in [
            range_high,
            range_low,
            equilibrium,
            range_start_index,
        ]
    ):
        range_high = float(range_high)
        range_low = float(range_low)
        equilibrium = float(equilibrium)
        range_start = max(
            get_index_time(
                data,
                range_start_index,
                first_time,
            ),
            first_time,
        )

        if range_start < last_time and range_high > range_low:
            fig.add_shape(
                type="rect",
                xref="x",
                yref="y",
                x0=range_start,
                x1=last_time,
                y0=range_low,
                y1=equilibrium,
                fillcolor="rgba(0, 205, 110, 0.055)",
                line={"width": 0},
                layer="below",
                legendgroup="trendmap",
                name="Discount Range",
                showlegend=False,
            )
            fig.add_shape(
                type="rect",
                xref="x",
                yref="y",
                x0=range_start,
                x1=last_time,
                y0=equilibrium,
                y1=range_high,
                fillcolor="rgba(255, 77, 91, 0.05)",
                line={"width": 0},
                layer="below",
                legendgroup="trendmap",
                name="Premium Range",
                showlegend=False,
            )
            fig.add_trace(
                go.Scatter(
                    x=[range_start, last_time],
                    y=[equilibrium, equilibrium],
                    mode="lines+text",
                    text=[None, "EQ"],
                    textposition="top left",
                    textfont={
                        "color": "#dce5ed",
                        "size": 10,
                    },
                    line={
                        "color": "rgba(220, 229, 237, 0.74)",
                        "width": 1.4,
                        "dash": "dash",
                    },
                    name="Dealing Range Equilibrium",
                    legendgroup="trendmap",
                    showlegend=True,
                    hovertemplate=(
                        "<b>Equilibrium</b><br>"
                        f"Level: {equilibrium:.2f}<extra></extra>"
                    ),
                )
            )

    level_specs = [
        (
            "Trend_Support",
            False,
            "Support",
            "#00bfa5",
            "S",
        ),
        (
            "Trend_Resistance",
            True,
            "Resistance",
            "#ff6e78",
            "R",
        ),
    ]

    for column, above_price, label, color, short_label in level_specs:
        levels = data[
            data[column].notna()
        ].copy()

        if above_price:
            levels = levels[
                levels[column].astype(float) >= current_price
            ]
        else:
            levels = levels[
                levels[column].astype(float) <= current_price
            ]

        if levels.empty:
            continue

        levels["_distance"] = (
            levels[column].astype(float) - current_price
        ).abs()

        for _, row in levels.nsmallest(2, "_distance").iterrows():
            level = float(row[column])
            start_time = max(row["time"], first_time)

            fig.add_trace(
                go.Scatter(
                    x=[start_time, last_time],
                    y=[level, level],
                    mode="lines+text",
                    text=[None, f"{short_label} {level:.2f}"],
                    textposition="top left",
                    textfont={
                        "color": color,
                        "size": 9,
                    },
                    line={
                        "color": color,
                        "width": 1.3,
                        "dash": "longdash",
                    },
                    name=f"Confirmed {label}",
                    legendgroup="trendmap",
                    showlegend=False,
                    hovertemplate=(
                        f"<b>Confirmed {label}</b><br>"
                        f"Level: {level:.2f}<br>"
                        f"Pivot: {row['time']}<extra></extra>"
                    ),
                )
            )

    label_rows = data[
        (data["time"] >= first_time)
        & data["Trend_SwingLabel"].notna()
    ].tail(MAX_SWING_MARKERS)

    for column, position in [
        ("Trend_Resistance", "top center"),
        ("Trend_Support", "bottom center"),
    ]:
        selected = label_rows[
            label_rows[column].notna()
        ]

        if selected.empty:
            continue

        fig.add_trace(
            go.Scatter(
                x=selected["time"],
                y=selected[column],
                mode="text",
                text=selected["Trend_SwingLabel"],
                textposition=position,
                textfont={
                    "color": "rgba(220, 230, 238, 0.80)",
                    "size": 9,
                },
                name="HH / HL / LH / LL",
                legendgroup="trendmap",
                showlegend=False,
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "Level: %{y:.2f}<br>"
                    "Time: %{x}<extra></extra>"
                ),
            )
        )


def select_chart_zones(
    data: pd.DataFrame,
    signal_column: str,
    top_column: str,
    bottom_column: str,
    mitigation_column: str,
    current_price: float,
    first_time,
    maximum_zones: int,
) -> pd.DataFrame:
    """Select nearby active zones plus recently mitigated zones."""

    required_columns = {
        signal_column,
        top_column,
        bottom_column,
    }

    if not required_columns.issubset(data.columns):
        return pd.DataFrame()

    zones = data[
        data[signal_column].fillna(0).ne(0)
        & data[top_column].notna()
        & data[bottom_column].notna()
    ].copy()

    if zones.empty:
        return zones

    zones["_middle"] = (
        zones[top_column].astype(float)
        + zones[bottom_column].astype(float)
    ) / 2

    zones["_distance"] = (
        zones["_middle"] - current_price
    ).abs()

    if mitigation_column in zones.columns:
        mitigation = pd.to_numeric(
            zones[mitigation_column],
            errors="coerce",
        ).fillna(0)
    else:
        mitigation = pd.Series(
            0,
            index=zones.index,
        )

    zones["_active"] = mitigation.le(0)
    zones["_end_index"] = mitigation.where(
        mitigation.gt(0),
        len(data) - 1,
    ).clip(
        lower=0,
        upper=len(data) - 1,
    ).astype(int)

    zones["_end_time"] = zones[
        "_end_index"
    ].map(
        data["time"].reset_index(drop=True)
    )

    zones = zones[
        zones["_end_time"] >= first_time
    ].copy()

    if zones.empty:
        return zones

    active_limit = max(
        1,
        maximum_zones // 2,
    )

    active = zones[
        zones["_active"]
    ].nsmallest(
        active_limit,
        "_distance",
    )

    completed_limit = max(
        0,
        maximum_zones - len(active),
    )

    completed = zones[
        ~zones["_active"]
    ].nlargest(
        completed_limit,
        "_end_index",
    )

    return (
        pd.concat([active, completed])
        .drop_duplicates()
        .sort_index()
    )


def select_nearest_active_liquidity(
    data: pd.DataFrame,
    current_price: float,
    maximum_levels: int,
) -> pd.DataFrame:
    """Select active liquidity levels nearest to price."""

    required_columns = {
        "Liquidity_Liquidity",
        "Liquidity_Level",
    }

    if not required_columns.issubset(data.columns):
        return pd.DataFrame()

    levels = data[
        data["Liquidity_Liquidity"].fillna(0).ne(0)
        & data["Liquidity_Level"].notna()
    ].copy()

    if levels.empty:
        return levels

    if "Liquidity_Swept" in levels.columns:
        levels = levels[
            levels["Liquidity_Swept"].apply(
                indicator_is_active
            )
        ].copy()

    if levels.empty:
        return levels

    levels["_distance"] = (
        levels["Liquidity_Level"].astype(float)
        - current_price
    ).abs()

    return (
        levels
        .nsmallest(maximum_levels, "_distance")
        .sort_index()
    )


def add_zone_trace(
    fig: go.Figure,
    start_time,
    end_time,
    bottom: float,
    top: float,
    label: str,
    legend_group: str,
    fill_color: str,
    border_color: str,
    show_legend: bool,
    display_text: str | None = None,
    details: str = "",
) -> None:
    """
    Draw a zone without permanent text.

    Zone information appears when the mouse is placed
    over the rectangle.
    """

    bottom = float(bottom)
    top = float(top)

    if top < bottom:
        top, bottom = bottom, top

    if start_time >= end_time:
        return

    short_labels = {
        "Bullish FVG": "FVG ▲",
        "Bearish FVG": "FVG ▼",
        "Bullish Order Block": "OB ▲",
        "Bearish Order Block": "OB ▼",
    }

    short_label = short_labels.get(
        label,
        label,
    )

    if display_text is not None:
        short_label = display_text

    fig.add_trace(
        go.Scatter(
            x=[
                start_time,
                end_time,
                end_time,
                start_time,
                start_time,
            ],
            y=[
                bottom,
                bottom,
                top,
                top,
                bottom,
            ],
            mode="lines",
            fill="toself",
            fillcolor=fill_color,
            line={
                "color": border_color,
                "width": 2.1,
            },
            name=label,
            legendgroup=legend_group,
            showlegend=show_legend,
            hoveron="fills",
            hovertemplate=(
                f"<b>{label}</b><br>"
                f"Top: {top:.2f}<br>"
                f"Bottom: {bottom:.2f}<br>"
                f"Size: {top - bottom:.2f}<br>"
                f"Created: {start_time}<br>"
                f"{details}"
                "<extra></extra>"
            ),
        )
    )

    middle_time = (
        start_time
        + (end_time - start_time) / 2
    )
    middle_price = (top + bottom) / 2

    fig.add_trace(
        go.Scatter(
            x=[middle_time],
            y=[middle_price],
            mode="text",
            text=[f"<b>{short_label}</b>"],
            textposition="middle center",
            textfont={
                "color": border_color,
                "size": 10,
            },
            name=f"{label} label",
            legendgroup=legend_group,
            showlegend=False,
            hoverinfo="skip",
        )
    )


def format_compact_number(
    value,
) -> str:
    """Format large indicator values for compact labels."""

    value = float(value)

    for threshold, suffix in [
        (1_000_000_000_000, "T"),
        (1_000_000_000, "B"),
        (1_000_000, "M"),
        (1_000, "k"),
    ]:
        if abs(value) >= threshold:
            return f"{value / threshold:.2f}{suffix}"

    return f"{value:.0f}"


def add_structure_markers(
    fig: go.Figure,
    data: pd.DataFrame,
    first_time,
    signal_column: str,
    definitions,
) -> None:
    """Draw each structure level from formation to break."""

    required_columns = {
        signal_column,
        "Structure_Level",
        "Structure_BrokenIndex",
    }

    if not required_columns.issubset(data.columns):
        return

    rows = data[
        data[signal_column].fillna(0).ne(0)
        & data["Structure_Level"].notna()
    ]

    for direction, label, color, symbol in definitions:

        selected = rows[
            rows[signal_column] == direction
        ]

        show_legend = True

        for _, row in selected.iterrows():

            signal_time = get_index_time(
                data,
                row["Structure_BrokenIndex"],
                row["time"],
            )

            if signal_time < first_time:
                continue

            level = float(
                row["Structure_Level"]
            )

            start_time = max(
                row["time"],
                first_time,
            )

            middle_time = (
                start_time
                + (signal_time - start_time) / 2
            )

            fig.add_trace(
                go.Scatter(
                    x=[
                        start_time,
                        middle_time,
                        signal_time,
                    ],
                    y=[level, level, level],
                    mode="lines+markers+text",
                    line={
                        "color": color,
                        "width": 2.1,
                        "dash": (
                            "dash"
                            if signal_column == "Structure_CHOCH"
                            else "solid"
                        ),
                    },
                    marker={
                        "symbol": symbol,
                        "size": [0, 0, 14],
                        "color": color,
                        "line": {
                            "color": "white",
                            "width": 1.3,
                        },
                    },
                    text=[None, f"<b>{label}</b>", None],
                    textposition=(
                        "top center"
                        if direction == 1
                        else "bottom center"
                    ),
                    textfont={
                        "color": color,
                        "size": 10,
                    },
                    name=label,
                    legendgroup=f"structure_{label}",
                    showlegend=show_legend,
                    hovertemplate=(
                        f"<b>{label}</b><br>"
                        f"Level: {level:.2f}<br>"
                        f"Formed: {row['time']}<br>"
                        f"Broken: {signal_time}"
                        "<extra></extra>"
                    ),
                )
            )

            show_legend = False


def add_liquidity_sweeps(
    fig: go.Figure,
    data: pd.DataFrame,
    first_time,
    last_time,
    maximum_sweeps: int = 10,
) -> None:
    """Draw recent paths from liquidity pools to sweep candles."""

    required_columns = {
        "Liquidity_Liquidity",
        "Liquidity_Level",
        "Liquidity_End",
        "Liquidity_Swept",
    }

    if not required_columns.issubset(data.columns):
        return

    rows = data[
        data["Liquidity_Liquidity"].fillna(0).ne(0)
        & data["Liquidity_Level"].notna()
        & data["Liquidity_End"].notna()
        & data["Liquidity_Swept"].fillna(0).gt(0)
    ].tail(maximum_sweeps * 3)

    visible_rows = []

    for _, row in rows.iterrows():
        swept_time = get_index_time(
            data,
            row["Liquidity_Swept"],
            row["time"],
        )

        if first_time <= swept_time <= last_time:
            visible_rows.append(row)

    show_legend = True

    for row in visible_rows[-maximum_sweeps:]:
        swept_index = int(
            row["Liquidity_Swept"]
        )

        end_time = max(
            get_index_time(
                data,
                row["Liquidity_End"],
                row["time"],
            ),
            first_time,
        )

        swept_time = get_index_time(
            data,
            swept_index,
            row["time"],
        )

        level = float(
            row["Liquidity_Level"]
        )

        target = float(
            data.iloc[swept_index][
                "high"
                if row["Liquidity_Liquidity"] == 1
                else "low"
            ]
        )

        middle_time = (
            end_time
            + (swept_time - end_time) / 2
        )

        middle_price = (level + target) / 2

        fig.add_trace(
            go.Scatter(
                x=[end_time, middle_time, swept_time],
                y=[level, middle_price, target],
                mode="lines+markers+text",
                line={
                    "color": "rgba(255, 70, 85, 0.88)",
                    "width": 2,
                    "dash": "dash",
                },
                marker={
                    "size": [0, 0, 9],
                    "color": "#ff4655",
                    "symbol": "x",
                },
                text=[None, "<b>SWEEP</b>", None],
                textposition="top center",
                textfont={
                    "color": "#ff6b78",
                    "size": 9,
                },
                name="Liquidity Sweep",
                legendgroup="liquidity_sweeps",
                showlegend=show_legend,
                hovertemplate=(
                    "<b>Liquidity Sweep</b><br>"
                    f"Level: {level:.2f}<br>"
                    f"Sweep price: {target:.2f}<br>"
                    f"Time: {swept_time}"
                    "<extra></extra>"
                ),
            )
        )

        show_legend = False


def add_previous_level_segments(
    fig: go.Figure,
    data: pd.DataFrame,
) -> None:
    """Draw historical previous four-hour highs and lows."""

    definitions = [
        ("FourHour_PreviousHigh", "PH", "#a9b4bf"),
        ("FourHour_PreviousLow", "PL", "#7f8b97"),
    ]

    show_legend = True

    for column, label, color in definitions:
        if column not in data.columns:
            continue

        values = data[column]
        groups = values.ne(values.shift()).cumsum()

        for _, rows in data[
            values.notna()
        ].groupby(groups[values.notna()]):
            level = float(rows[column].iloc[0])
            start_time = rows["time"].iloc[0]
            end_time = (
                rows["time"].iloc[-1]
                + pd.Timedelta(minutes=TIMEFRAME_MINUTES)
            )

            fig.add_trace(
                go.Scatter(
                    x=[start_time, end_time],
                    y=[level, level],
                    mode="lines+text",
                    text=[None, f"<b>{label}</b>"],
                    textposition=(
                        "top left"
                        if label == "PH"
                        else "bottom left"
                    ),
                    textfont={
                        "color": color,
                        "size": 9,
                    },
                    line={
                        "color": color,
                        "width": 1.25,
                        "dash": "dot",
                    },
                    opacity=0.58,
                    name="Previous 4H Levels",
                    legendgroup="previous_4h",
                    showlegend=show_legend,
                    hovertemplate=(
                        f"<b>{label}</b><br>"
                        f"Level: {level:.2f}"
                        "<extra></extra>"
                    ),
                )
            )

            show_legend = False


def add_retracement_annotations(
    fig: go.Figure,
    data: pd.DataFrame,
    maximum_labels: int = 8,
) -> None:
    """Label completed swing legs with current/deepest retracement."""

    required_columns = {
        "Retracement_Direction",
        "Retracement_CurrentRetracement%",
        "Retracement_DeepestRetracement%",
    }

    if not required_columns.issubset(data.columns):
        return

    direction = data["Retracement_Direction"].fillna(0)
    turns = data[
        direction.ne(0)
        & direction.ne(direction.shift(-1).fillna(0))
    ].tail(maximum_labels)

    if turns.empty:
        return

    x_values = []
    y_values = []
    labels = []
    hover_values = []

    for _, row in turns.iterrows():
        current = float(
            row["Retracement_CurrentRetracement%"]
        )
        deepest = float(
            row["Retracement_DeepestRetracement%"]
        )

        x_values.append(row["time"])
        y_values.append(
            row["high"]
            if row["Retracement_Direction"] == -1
            else row["low"]
        )
        labels.append(
            f"C:{current:.1f}%<br>D:{deepest:.1f}%"
        )
        hover_values.append(
            f"Current: {current:.1f}%<br>"
            f"Deepest: {deepest:.1f}%"
        )

    fig.add_trace(
        go.Scatter(
            x=x_values,
            y=y_values,
            mode="markers+text",
            marker={
                "size": 5,
                "color": "#d7dde4",
            },
            text=labels,
            textposition="top center",
            textfont={
                "color": "rgba(225, 232, 240, 0.72)",
                "size": 9,
            },
            name="Retracement Turns",
            legendgroup="retracements",
            hovertext=hover_values,
            hoverinfo="text",
        )
    )


# =========================================================
# CREATE MPLFINANCE SNAPSHOT
# =========================================================

def create_mplfinance_snapshot(
    results: pd.DataFrame,
    number_of_candles: int = MPLFINANCE_CANDLES,
) -> Path:
    """Create a clean high-resolution SMC chart with mplfinance."""

    data = results.copy().reset_index(drop=True)
    data["time"] = pd.to_datetime(data["time"])

    chart_data = data.tail(number_of_candles).copy()

    if chart_data.empty:
        raise ValueError(
            "No candles are available for the mplfinance snapshot."
        )

    first_index = int(chart_data.index[0])
    last_index = int(chart_data.index[-1])
    candle_total = len(chart_data)
    first_time = chart_data["time"].iloc[0]
    current_price = float(chart_data["close"].iloc[-1])

    ohlcv = (
        chart_data
        .set_index("time")
        [["open", "high", "low", "close", "volume"]]
        .rename(
            columns={
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
            }
        )
    )

    market_colors = mpf.make_marketcolors(
        up="#19c9a5",
        down="#ff4d5b",
        edge="inherit",
        wick={
            "up": "#4ee8c8",
            "down": "#ff7480",
        },
        volume={
            "up": "#167f6d",
            "down": "#9f3742",
        },
    )

    chart_style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=market_colors,
        facecolor="#101215",
        figcolor="#101215",
        gridcolor="#29313a",
        gridstyle="-",
        y_on_right=True,
        rc={
            "axes.edgecolor": "#46515c",
            "axes.labelcolor": "#c9d4de",
            "text.color": "#e8eef4",
            "xtick.color": "#a9b5c0",
            "ytick.color": "#a9b5c0",
            "font.size": 9,
        },
    )

    figure, axes = mpf.plot(
        ohlcv,
        type="candle",
        style=chart_style,
        volume=True,
        returnfig=True,
        figsize=(18, 10),
        panel_ratios=(5, 1),
        datetime_format="%b %d %H:%M",
        xrotation=0,
        ylabel="Gold price",
        ylabel_lower="Tick volume",
        scale_width_adjustment={
            "candle": 0.85,
            "volume": 0.75,
        },
    )

    price_axis = axes[0]

    # Session rectangles stay behind the SMC zones and candles.
    for session_name, session_color in {
        "London": "#2d91ff",
        "NewYork": "#ff9b2d",
    }.items():
        active_column = f"Session_{session_name}_Active"
        high_column = f"Session_{session_name}_High"
        low_column = f"Session_{session_name}_Low"

        if not {
            active_column,
            high_column,
            low_column,
        }.issubset(chart_data.columns):
            continue

        active = chart_data[active_column].fillna(0).eq(1)
        session_groups = active.ne(active.shift()).cumsum()

        for _, session_rows in chart_data[active].groupby(
            session_groups[active]
        ):
            positive_lows = session_rows.loc[
                session_rows[low_column] > 0,
                low_column,
            ]

            if positive_lows.empty:
                continue

            x_start = float(session_rows.index[0] - first_index) - 0.45
            x_end = float(session_rows.index[-1] - first_index) + 0.45
            session_low = float(positive_lows.min())
            session_high = float(session_rows[high_column].max())

            price_axis.add_patch(
                Rectangle(
                    (x_start, session_low),
                    max(x_end - x_start, 0.2),
                    max(session_high - session_low, 0.01),
                    facecolor=to_rgba(session_color, 0.045),
                    edgecolor=to_rgba(session_color, 0.18),
                    linewidth=0.7,
                    zorder=0.4,
                )
            )

    latest_structure = chart_data.iloc[-1]

    if all(
        pd.notna(latest_structure.get(column))
        for column in [
            "Trend_RangeHigh",
            "Trend_RangeLow",
            "Trend_Equilibrium",
            "Trend_RangeStartIndex",
        ]
    ):
        range_high = float(latest_structure["Trend_RangeHigh"])
        range_low = float(latest_structure["Trend_RangeLow"])
        equilibrium = float(latest_structure["Trend_Equilibrium"])
        range_start = max(
            float(latest_structure["Trend_RangeStartIndex"]) - first_index,
            -0.5,
        )
        range_width = candle_total - 0.5 - range_start

        if range_high > range_low and range_width > 0:
            price_axis.add_patch(
                Rectangle(
                    (range_start, range_low),
                    range_width,
                    equilibrium - range_low,
                    facecolor=to_rgba("#00cd6e", 0.045),
                    edgecolor="none",
                    zorder=0.55,
                )
            )
            price_axis.add_patch(
                Rectangle(
                    (range_start, equilibrium),
                    range_width,
                    range_high - equilibrium,
                    facecolor=to_rgba("#ff4d5b", 0.04),
                    edgecolor="none",
                    zorder=0.55,
                )
            )
            price_axis.plot(
                [range_start, candle_total - 0.5],
                [equilibrium, equilibrium],
                color=to_rgba("#dce5ed", 0.72),
                linewidth=0.9,
                linestyle=(0, (5, 4)),
                zorder=1.7,
            )
            price_axis.text(
                candle_total - 1.0,
                equilibrium,
                f"EQ {equilibrium:.2f}",
                color="#dce5ed",
                fontsize=6.7,
                ha="right",
                va="bottom",
                clip_on=True,
                zorder=3.4,
            )

    zone_specs = [
        {
            "signal": "FVG_FVG",
            "top": "FVG_Top",
            "bottom": "FVG_Bottom",
            "mitigation": "FVG_MitigatedIndex",
            "maximum": max(MAX_FVG_ZONES, 20),
            "name": "FVG",
            "bull": "#00dc79",
            "bear": "#ff4d5b",
            "hatch": None,
        },
        {
            "signal": "OB_OB",
            "top": "OB_Top",
            "bottom": "OB_Bottom",
            "mitigation": "OB_MitigatedIndex",
            "maximum": max(MAX_OB_ZONES, 12),
            "name": "OB",
            "bull": "#5d9cff",
            "bear": "#ff9b2d",
            "hatch": "///",
        },
    ]

    for spec in zone_specs:
        zones = select_chart_zones(
            data=data,
            signal_column=spec["signal"],
            top_column=spec["top"],
            bottom_column=spec["bottom"],
            mitigation_column=spec["mitigation"],
            current_price=current_price,
            first_time=first_time,
            maximum_zones=spec["maximum"],
        )

        for source_index, row in zones.iterrows():
            bullish = float(row[spec["signal"]]) == 1
            active_zone = bool(row["_active"])
            zone_color = spec["bull"] if bullish else spec["bear"]
            end_index = (
                last_index
                if active_zone
                else int(row["_end_index"])
            )
            x_start = max(float(source_index - first_index) - 0.42, -0.5)
            x_end = min(float(end_index - first_index) + 0.42, candle_total - 0.5)
            bottom = float(row[spec["bottom"]])
            top = float(row[spec["top"]])

            if top < bottom:
                top, bottom = bottom, top

            if x_end <= x_start or top <= bottom:
                continue

            price_axis.add_patch(
                Rectangle(
                    (x_start, bottom),
                    x_end - x_start,
                    top - bottom,
                    facecolor=to_rgba(
                        zone_color,
                        0.17 if active_zone else 0.065,
                    ),
                    edgecolor=to_rgba(
                        zone_color,
                        0.9 if active_zone else 0.38,
                    ),
                    linewidth=1.15 if active_zone else 0.8,
                    linestyle="-" if active_zone else "--",
                    hatch=spec["hatch"] if active_zone else None,
                    zorder=1.0,
                )
            )

            if x_start >= -0.5 and x_end - x_start >= 7:
                direction = "UP" if bullish else "DOWN"
                status = "" if active_zone else " filled"
                price_axis.text(
                    x_start + 0.8,
                    top,
                    f"{spec['name']} {direction}{status}",
                    color=to_rgba(zone_color, 0.95),
                    fontsize=6.8,
                    fontweight="bold",
                    va="bottom",
                    clip_on=True,
                    zorder=3.2,
                )

    liquidity_rows = select_nearest_active_liquidity(
        data=data,
        current_price=current_price,
        maximum_levels=MAX_LIQUIDITY_LEVELS,
    )

    for source_index, row in liquidity_rows.iterrows():
        level = float(row["Liquidity_Level"])
        bullish = float(row["Liquidity_Liquidity"]) == 1
        line_color = "#d95cff" if bullish else "#ffee00"
        label = "BSL" if bullish else "SSL"
        x_start = max(float(source_index - first_index), 0)

        price_axis.plot(
            [x_start, candle_total - 0.5],
            [level, level],
            color=line_color,
            linewidth=1.2,
            linestyle=(0, (4, 3)),
            alpha=0.88,
            zorder=2.1,
        )
        price_axis.text(
            candle_total - 1.0,
            level,
            label,
            color=line_color,
            fontsize=7,
            fontweight="bold",
            ha="right",
            va="bottom",
            clip_on=True,
            zorder=3.5,
        )

    for column, above_price, label, color in [
        ("Trend_Support", False, "S", "#00bfa5"),
        ("Trend_Resistance", True, "R", "#ff6e78"),
    ]:
        levels = data[data[column].notna()].copy()
        levels = (
            levels[levels[column].astype(float) >= current_price]
            if above_price
            else levels[levels[column].astype(float) <= current_price]
        )

        if levels.empty:
            continue

        levels["_distance"] = (
            levels[column].astype(float) - current_price
        ).abs()

        for source_index, row in levels.nsmallest(2, "_distance").iterrows():
            level = float(row[column])
            x_start = max(float(source_index - first_index), 0)

            price_axis.plot(
                [x_start, candle_total - 0.5],
                [level, level],
                color=to_rgba(color, 0.80),
                linewidth=0.9,
                linestyle=(0, (7, 4)),
                zorder=1.9,
            )
            price_axis.text(
                candle_total - 1.0,
                level,
                f"{label} {level:.2f}",
                color=color,
                fontsize=6.7,
                ha="right",
                va="bottom",
                clip_on=True,
                zorder=3.4,
            )

    visible_swings = chart_data[
        chart_data["Swing_HighLow"].fillna(0).ne(0)
        & chart_data["Swing_Level"].notna()
    ].tail(MAX_SWING_MARKERS)

    if not visible_swings.empty:
        swing_x = visible_swings.index.to_numpy() - first_index
        swing_y = visible_swings["Swing_Level"].astype(float).to_numpy()

        price_axis.plot(
            swing_x,
            swing_y,
            color=to_rgba("#d2d9e0", 0.43),
            linewidth=0.95,
            zorder=2.0,
        )

        for direction, color, marker in [
            (1, "#69d7ff", "v"),
            (-1, "#ff77d4", "^"),
        ]:
            selected = visible_swings[
                visible_swings["Swing_HighLow"] == direction
            ]

            price_axis.scatter(
                selected.index.to_numpy() - first_index,
                selected["Swing_Level"].astype(float),
                s=24,
                marker=marker,
                facecolors="none",
                edgecolors=color,
                linewidths=1.0,
                zorder=3.0,
            )

            for source_index, row in selected.iterrows():
                swing_label = row.get("Trend_SwingLabel")

                if pd.isna(swing_label):
                    continue

                price_axis.annotate(
                    str(swing_label),
                    (
                        source_index - first_index,
                        float(row["Swing_Level"]),
                    ),
                    xytext=(0, 7 if direction == 1 else -9),
                    textcoords="offset points",
                    ha="center",
                    va="bottom" if direction == 1 else "top",
                    color=to_rgba("#dce6ee", 0.82),
                    fontsize=6.2,
                    fontweight="bold",
                    clip_on=True,
                    zorder=3.2,
                )

    structure_rows = data[
        (
            data["Structure_BOS"].fillna(0).ne(0)
            | data["Structure_CHOCH"].fillna(0).ne(0)
        )
        & data["Structure_BrokenIndex"].notna()
        & data["Structure_Level"].notna()
    ].tail(24)

    for _, row in structure_rows.iterrows():
        broken_index = int(row["Structure_BrokenIndex"])

        if not first_index <= broken_index <= last_index:
            continue

        bos_value = row["Structure_BOS"]
        is_bos = pd.notna(bos_value) and float(bos_value) != 0
        direction_value = (
            float(bos_value)
            if is_bos
            else float(row["Structure_CHOCH"])
        )
        label = "BOS" if is_bos else "CHoCH"
        color = (
            "#00e676" if is_bos and direction_value == 1
            else "#ff1744" if is_bos
            else "#00e5ff" if direction_value == 1
            else "#ffb300"
        )
        marker = "^" if direction_value == 1 else "v"
        level = float(row["Structure_Level"])
        x_value = broken_index - first_index

        price_axis.scatter(
            [x_value],
            [level],
            s=34,
            marker=marker if is_bos else "D",
            facecolors=to_rgba(color, 0.28),
            edgecolors=color,
            linewidths=1.1,
            zorder=3.8,
        )
        price_axis.annotate(
            label,
            (x_value, level),
            xytext=(0, 8 if direction_value == 1 else -11),
            textcoords="offset points",
            ha="center",
            va="bottom" if direction_value == 1 else "top",
            color=color,
            fontsize=6.8,
            fontweight="bold",
            clip_on=True,
            zorder=4.0,
        )

    for column, label, color in [
        ("Daily_PreviousHigh", "PDH", "#00e5ff"),
        ("Daily_PreviousLow", "PDL", "#ff55eb"),
        ("FourHour_PreviousHigh", "P4H", "#aeb8c2"),
        ("FourHour_PreviousLow", "P4L", "#7f8b97"),
    ]:
        value = latest_value(chart_data, column)

        if value is None or pd.isna(value):
            continue

        level = float(value)
        price_axis.axhline(
            level,
            color=color,
            linewidth=0.9,
            linestyle=(0, (6, 4)),
            alpha=0.72,
            zorder=1.8,
        )
        price_axis.text(
            candle_total - 1.0,
            level,
            f"{label} {level:.2f}",
            color=color,
            fontsize=6.7,
            ha="right",
            va="bottom",
            clip_on=True,
            zorder=3.4,
        )

    price_axis.axhline(
        current_price,
        color="#f4f7fa",
        linewidth=1.0,
        linestyle=(0, (2, 3)),
        alpha=0.9,
        zorder=2.2,
    )
    price_axis.text(
        candle_total - 1.0,
        current_price,
        f"PRICE {current_price:.2f}",
        color="#ffffff",
        fontsize=7.2,
        fontweight="bold",
        ha="right",
        va="bottom",
        clip_on=True,
        zorder=4.0,
    )

    candle_low = float(chart_data["low"].min())
    candle_high = float(chart_data["high"].max())
    padding = max((candle_high - candle_low) * 0.065, 0.5)
    price_axis.set_ylim(
        candle_low - padding,
        candle_high + padding,
    )
    price_axis.set_xlim(-1.0, candle_total + 2.0)
    price_axis.set_title(
        f"{SYMBOL} {TIMEFRAME_NAME} | Smart Money Concepts | "
        f"Completed {chart_data['time'].iloc[-1]}",
        color="#f0f5f9",
        fontsize=14,
        fontweight="bold",
        loc="left",
        pad=12,
    )

    legend_handles = [
        Patch(
            facecolor=to_rgba("#00dc79", 0.18),
            edgecolor="#00dc79",
            label="Bullish FVG",
        ),
        Patch(
            facecolor=to_rgba("#ff4d5b", 0.18),
            edgecolor="#ff4d5b",
            label="Bearish FVG",
        ),
        Patch(
            facecolor=to_rgba("#5d9cff", 0.18),
            edgecolor="#5d9cff",
            hatch="///",
            label="Bullish OB",
        ),
        Patch(
            facecolor=to_rgba("#ff9b2d", 0.18),
            edgecolor="#ff9b2d",
            hatch="///",
            label="Bearish OB",
        ),
        Line2D(
            [0],
            [0],
            color="#d95cff",
            linestyle="--",
            label="Buy-side liquidity",
        ),
        Line2D(
            [0],
            [0],
            color="#ffee00",
            linestyle="--",
            label="Sell-side liquidity",
        ),
        Line2D(
            [0],
            [0],
            color="#00bfa5",
            linestyle="--",
            label="Confirmed support",
        ),
        Line2D(
            [0],
            [0],
            color="#ff6e78",
            linestyle="--",
            label="Confirmed resistance",
        ),
        Line2D(
            [0],
            [0],
            color="#dce5ed",
            linestyle="--",
            label="Range equilibrium",
        ),
    ]
    legend = price_axis.legend(
        handles=legend_handles,
        loc="upper left",
        ncol=3,
        frameon=True,
        fontsize=7.5,
        borderpad=0.7,
    )
    legend.get_frame().set_facecolor(to_rgba("#101215", 0.88))
    legend.get_frame().set_edgecolor("#46515c")

    figure.text(
        0.08,
        0.025,
        retracement_status(chart_data)
        + f" | {latest_structure.get('Trend_State', 'Unavailable')}"
        + f" · {latest_structure.get('Trend_Zone', 'Unavailable')}"
        + " | Shaded rectangles are separate zones; faded dashed zones are mitigated.",
        color="#aab6c1",
        fontsize=8.5,
    )
    figure.subplots_adjust(
        left=0.06,
        right=0.93,
        top=0.91,
        bottom=0.10,
        hspace=0.08,
    )

    output_path = Path(MPLFINANCE_OUTPUT_FILE).resolve()
    temporary_output_path = output_path.with_name(
        f"{output_path.stem}.tmp{output_path.suffix}"
    )

    try:
        figure.savefig(
            temporary_output_path,
            format="png",
            dpi=165,
            facecolor=figure.get_facecolor(),
            bbox_inches="tight",
        )
        temporary_output_path.replace(output_path)
    finally:
        plt.close(figure)
        temporary_output_path.unlink(missing_ok=True)

    return output_path


# =========================================================
# CREATE INTERACTIVE CHART
# =========================================================

def ensure_plotly_js_asset() -> Path:
    """Create the reusable local Plotly runtime when it is missing."""

    plotly_path = Path(
        PLOTLY_JS_FILE
    ).resolve()

    if plotly_path.exists() and plotly_path.stat().st_size > 1_000_000:
        return plotly_path

    temporary_path = plotly_path.with_suffix(
        ".tmp"
    )
    temporary_path.write_text(
        get_plotlyjs(),
        encoding="utf-8",
    )
    temporary_path.replace(
        plotly_path
    )

    return plotly_path


def defer_plotly_initialization(html: str) -> str:
    """Start the heavy Plotly runtime after the page shell is visible."""

    initializer = "window.PLOTLYENV=window.PLOTLYENV || {};"
    initializer_index = html.find(initializer)

    if initializer_index < 0:
        raise RuntimeError(
            "Could not locate the generated Plotly initialization script."
        )

    script_end = html.find(
        "</script>",
        initializer_index,
    )

    if script_end < 0:
        raise RuntimeError(
            "Could not locate the end of the Plotly initialization script."
        )

    return (
        html[:initializer_index]
        + 'window.addEventListener("smc-plotly-ready", () => {'
        + html[initializer_index:script_end]
        + "\n});\n"
        + html[script_end:]
    )


def create_interactive_chart(
    results: pd.DataFrame,
    number_of_candles: int = 1200,
    backtest_summary: dict | None = None,
) -> Path:
    """Create a clean interactive SMC chart."""

    data = results.copy()

    data["time"] = pd.to_datetime(
        data["time"]
    )

    chart_data = data.tail(
        number_of_candles
    ).copy()

    if chart_data.empty:
        raise ValueError(
            "No candles are available for the chart."
        )

    first_time = chart_data["time"].iloc[0]
    last_time = chart_data["time"].iloc[-1]

    current_price = float(
        chart_data["close"].iloc[-1]
    )

    status_text = retracement_status(
        chart_data
    )

    fig = go.Figure()

    # -----------------------------------------------------
    # CANDLESTICKS
    # -----------------------------------------------------

    fig.add_trace(
        go.Candlestick(
            x=chart_data["time"],
            open=chart_data["open"],
            high=chart_data["high"],
            low=chart_data["low"],
            close=chart_data["close"],
            name=f"{SYMBOL} {TIMEFRAME_NAME}",
            increasing_line_color="#19c9a5",
            increasing_fillcolor="#19c9a5",
            decreasing_line_color="#ff4d5b",
            decreasing_fillcolor="#ff4d5b",
            whiskerwidth=0.35,
        )
    )

    for session_name, fill_color in (
        SESSION_COLORS.items()
    ):
        add_session_regions(
            fig=fig,
            data=chart_data,
            session_name=session_name,
            fill_color=fill_color,
        )

    shown_groups = set()

    # -----------------------------------------------------
    # ACTIVE AND RECENTLY MITIGATED FAIR VALUE GAPS
    # -----------------------------------------------------

    fvg_zones = select_chart_zones(
        data=data,
        signal_column="FVG_FVG",
        top_column="FVG_Top",
        bottom_column="FVG_Bottom",
        mitigation_column="FVG_MitigatedIndex",
        current_price=current_price,
        first_time=first_time,
        maximum_zones=MAX_FVG_ZONES,
    )

    for _, row in fvg_zones.iterrows():

        bullish = row["FVG_FVG"] == 1
        is_active = bool(row["_active"])
        zone_end = (
            last_time
            if is_active
            else row["_end_time"]
        )

        if bullish:
            label = "Bullish FVG"
            group = "bullish_fvg"
            fill_color = (
                "rgba(0,205,110,0.27)"
                if is_active
                else "rgba(0,205,110,0.10)"
            )
            border_color = (
                "rgba(0,240,130,0.95)"
                if is_active
                else "rgba(0,240,130,0.46)"
            )
        else:
            label = "Bearish FVG"
            group = "bearish_fvg"
            fill_color = (
                "rgba(235,55,75,0.27)"
                if is_active
                else "rgba(235,55,75,0.10)"
            )
            border_color = (
                "rgba(255,75,95,0.95)"
                if is_active
                else "rgba(255,75,95,0.46)"
            )

        direction_symbol = "▲" if bullish else "▼"
        display_text = (
            f"FVG {direction_symbol}"
            if is_active
            else f"FVG {direction_symbol} · filled"
        )

        details = (
            "Status: Active<br>"
            if is_active
            else f"Status: Mitigated<br>Filled: {zone_end}<br>"
        )

        add_zone_trace(
            fig=fig,
            start_time=max(
                row["time"],
                first_time,
            ),
            end_time=zone_end,
            bottom=row["FVG_Bottom"],
            top=row["FVG_Top"],
            label=label,
            legend_group=group,
            fill_color=fill_color,
            border_color=border_color,
            show_legend=group not in shown_groups,
            display_text=display_text,
            details=details,
        )

        shown_groups.add(group)

    # -----------------------------------------------------
    # ACTIVE AND RECENTLY MITIGATED ORDER BLOCKS
    # -----------------------------------------------------

    order_block_zones = select_chart_zones(
        data=data,
        signal_column="OB_OB",
        top_column="OB_Top",
        bottom_column="OB_Bottom",
        mitigation_column="OB_MitigatedIndex",
        current_price=current_price,
        first_time=first_time,
        maximum_zones=MAX_OB_ZONES,
    )

    for _, row in order_block_zones.iterrows():

        bullish = row["OB_OB"] == 1
        is_active = bool(row["_active"])
        zone_end = (
            last_time
            if is_active
            else row["_end_time"]
        )

        if bullish:
            label = "Bullish Order Block"
            group = "bullish_ob"
            fill_color = (
                "rgba(35,125,255,0.27)"
                if is_active
                else "rgba(35,125,255,0.10)"
            )
            border_color = (
                "rgba(65,160,255,0.95)"
                if is_active
                else "rgba(65,160,255,0.46)"
            )
        else:
            label = "Bearish Order Block"
            group = "bearish_ob"
            fill_color = (
                "rgba(255,140,25,0.27)"
                if is_active
                else "rgba(255,140,25,0.10)"
            )
            border_color = (
                "rgba(255,175,45,0.95)"
                if is_active
                else "rgba(255,175,45,0.46)"
            )

        percentage = float(
            row.get("OB_Percentage", 0) or 0
        )

        volume = float(
            row.get("OB_OBVolume", 0) or 0
        )

        direction_symbol = "▲" if bullish else "▼"
        display_text = (
            f"OB {direction_symbol} {percentage:.0f}%"
            if is_active
            else f"OB {direction_symbol} {percentage:.0f}% · mitigated"
        )

        details = (
            f"Volume: {format_compact_number(volume)}<br>"
            f"Strength: {percentage:.1f}%<br>"
            + (
                "Status: Active<br>"
                if is_active
                else f"Status: Mitigated<br>Filled: {zone_end}<br>"
            )
        )

        add_zone_trace(
            fig=fig,
            start_time=max(
                row["time"],
                first_time,
            ),
            end_time=zone_end,
            bottom=row["OB_Bottom"],
            top=row["OB_Top"],
            label=label,
            legend_group=group,
            fill_color=fill_color,
            border_color=border_color,
            show_legend=group not in shown_groups,
            display_text=display_text,
            details=details,
        )

        shown_groups.add(group)

    # -----------------------------------------------------
    # ACTIVE LIQUIDITY
    # -----------------------------------------------------

    liquidity_rows = select_nearest_active_liquidity(
        data=data,
        current_price=current_price,
        maximum_levels=MAX_LIQUIDITY_LEVELS,
    )

    for _, row in liquidity_rows.iterrows():

        level = float(
            row["Liquidity_Level"]
        )

        start_time = max(
            row["time"],
            first_time,
        )

        if row["Liquidity_Liquidity"] == 1:
            label = "Buy-side Liquidity"
            short_label = "BSL"
            group = "buy_side_liquidity"
            line_color = "#d500f9"
        else:
            label = "Sell-side Liquidity"
            short_label = "SSL"
            group = "sell_side_liquidity"
            line_color = "#ffee00"

        fig.add_trace(
            go.Scatter(
                x=[
                    start_time,
                    last_time,
                ],
                y=[
                    level,
                    level,
                ],
                mode="lines+text",
                text=[
                    None,
                    f"<b>{short_label}</b>",
                ],
                textposition="top left",
                textfont={
                    "color": line_color,
                    "size": 11,
                },
                line={
                    "color": line_color,
                    "width": 2.6,
                    "dash": "dot",
                },
                name=label,
                legendgroup=group,
                showlegend=group not in shown_groups,
                hovertemplate=(
                    f"<b>{label}</b><br>"
                    f"Level: {level:.2f}<br>"
                    "<extra></extra>"
                ),
            )
        )

        shown_groups.add(group)

    add_liquidity_sweeps(
        fig=fig,
        data=data,
        first_time=first_time,
        last_time=last_time,
    )

    add_previous_level_segments(
        fig=fig,
        data=chart_data,
    )

    # -----------------------------------------------------
    # SWING HIGHS AND LOWS
    # -----------------------------------------------------

    add_swing_markers(
        fig=fig,
        data=chart_data,
    )

    add_structure_map(
        fig=fig,
        data=data,
        first_time=first_time,
        last_time=last_time,
        current_price=current_price,
    )

    # -----------------------------------------------------
    # BOS MARKERS
    # -----------------------------------------------------

    add_structure_markers(
        fig=fig,
        data=data,
        first_time=first_time,
        signal_column="Structure_BOS",
        definitions=[
            (
                1,
                "Bullish BOS",
                "#00e676",
                "triangle-up",
            ),
            (
                -1,
                "Bearish BOS",
                "#ff1744",
                "triangle-down",
            ),
        ],
    )

    # -----------------------------------------------------
    # CHoCH MARKERS
    # -----------------------------------------------------

    add_structure_markers(
        fig=fig,
        data=data,
        first_time=first_time,
        signal_column="Structure_CHOCH",
        definitions=[
            (
                1,
                "Bullish CHoCH",
                "#00e5ff",
                "diamond",
            ),
            (
                -1,
                "Bearish CHoCH",
                "#ffb300",
                "diamond",
            ),
        ],
    )

    add_retracement_annotations(
        fig=fig,
        data=chart_data,
    )

    # -----------------------------------------------------
    # PREVIOUS DAILY HIGH AND LOW
    # -----------------------------------------------------

    previous_high = latest_value(
        chart_data,
        "Daily_PreviousHigh",
    )

    previous_low = latest_value(
        chart_data,
        "Daily_PreviousLow",
    )

    if previous_high is not None:

        previous_high = float(
            previous_high
        )

        fig.add_hline(
            y=previous_high,
            line_color="#00e5ff",
            line_width=2.5,
            line_dash="dash",
            annotation_text=(
                f"PDH {previous_high:.2f}"
            ),
            annotation_position="top right",
            annotation_font={
                "color": "#00e5ff",
                "size": 12,
            },
            annotation_bgcolor="rgba(16,18,21,0.88)",
            annotation_bordercolor="#00e5ff",
            annotation_borderpad=4,
        )

    if previous_low is not None:

        previous_low = float(
            previous_low
        )

        fig.add_hline(
            y=previous_low,
            line_color="#ff00e6",
            line_width=2.5,
            line_dash="dash",
            annotation_text=(
                f"PDL {previous_low:.2f}"
            ),
            annotation_position="bottom right",
            annotation_font={
                "color": "#ff55eb",
                "size": 12,
            },
            annotation_bgcolor="rgba(16,18,21,0.88)",
            annotation_bordercolor="#ff00e6",
            annotation_borderpad=4,
        )

    # -----------------------------------------------------
    # CURRENT PRICE
    # -----------------------------------------------------

    fig.add_hline(
        y=current_price,
        line_color="white",
        line_width=1.8,
        line_dash="dot",
        annotation_text=(
            f"Price {current_price:.2f}"
        ),
        annotation_position="bottom right",
        annotation_font={
            "color": "white",
            "size": 12,
        },
        annotation_bgcolor="rgba(16,18,21,0.92)",
        annotation_bordercolor="white",
        annotation_borderpad=4,
    )

    # -----------------------------------------------------
    # LAYOUT
    # -----------------------------------------------------

    fig.update_layout(
        title={
            "text": (
                f"<b>{SYMBOL} {TIMEFRAME_NAME}</b>"
                "<br>"
                "<sup>"
                "Hover or tap for details · Drag to pan · "
                "Use Indicators to organize overlays"
                "</sup>"
            ),
            "x": 0.5,
            "xanchor": "center",
        },
        template="plotly_dark",
        autosize=True,
        hovermode="closest",
        dragmode="pan",
        uirevision="smc-responsive-chart",
        hoverdistance=30,
        spikedistance=-1,
        showlegend=False,
        paper_bgcolor="#101215",
        plot_bgcolor="#101215",
        xaxis_title="Time",
        yaxis_title="Gold Price",
        font={
            "family": "Arial",
            "size": 13,
        },
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "center",
            "x": 0.5,
            "groupclick": "togglegroup",
            "bgcolor": "rgba(15,15,15,0.80)",
            "bordercolor": "#555",
            "borderwidth": 1,
            "font": {
                "size": 11,
            },
        },
        margin={
            "l": 80,
            "r": 150,
            "t": 140,
            "b": 90,
        },
        hoverlabel={
            "bgcolor": "#20242a",
            "font_size": 12,
        },
    )

    latest_window_start = max(
        first_time,
        last_time - pd.Timedelta(days=3),
    )

    chart_range_end = (
        last_time
        + pd.Timedelta(
            minutes=TIMEFRAME_MINUTES * 3
        )
    )

    default_price_data = chart_data[
        chart_data["time"] >= latest_window_start
    ]
    default_price_low = float(
        default_price_data["low"].min()
    )
    default_price_high = float(
        default_price_data["high"].max()
    )
    default_price_span = max(
        default_price_high - default_price_low,
        default_price_high * 0.002,
    )
    default_price_padding = default_price_span * 0.08

    fig.update_xaxes(
        range=[
            latest_window_start,
            chart_range_end,
        ],
        rangebreaks=[
            {
                "bounds": [
                    "sat",
                    "mon",
                ],
            }
        ],
        rangeslider={
            "visible": True,
            "thickness": 0.07,
        },
        gridcolor="#29313a",
        showspikes=True,
        spikecolor="#dddddd",
        spikemode="across",
    )

    fig.update_yaxes(
        range=[
            default_price_low - default_price_padding,
            default_price_high + default_price_padding,
        ],
        autorange=False,
        gridcolor="#29313a",
        tickformat=".2f",
        fixedrange=False,
        showspikes=True,
        spikecolor="#dddddd",
        spikemode="across",
    )

    output_path = Path(
        HTML_OUTPUT_FILE
    ).resolve()

    chart_config = {
        "responsive": True,
        "scrollZoom": True,
        "displaylogo": False,
        "doubleClick": "reset+autosize",
        "showTips": True,
        "modeBarButtonsToAdd": [
            "drawline",
            "drawrect",
            "eraseshape",
        ],
        "modeBarButtonsToRemove": [
            "select2d",
            "lasso2d",
        ],
        "toImageButtonOptions": {
            "format": "png",
            "filename": "xauusd_m15_smc_chart",
            "width": 2200,
            "height": 1200,
            "scale": 2,
        },
    }

    responsive_script = """
(() => {
    const plot = document.getElementById("smc-chart");
    const shell = document.getElementById("chart-shell");
    const loadingOverlay = document.getElementById("chart-loading");
    const previewImage = document.getElementById("chart-preview");
    const indicatorButton = document.getElementById("chart-indicators");
    const indicatorPanel = document.getElementById("indicator-panel");
    const closePanelButton = document.getElementById("close-indicators");
    const oneDayButton = document.getElementById("chart-1d");
    const latestButton = document.getElementById("chart-latest");
    const oneWeekButton = document.getElementById("chart-1w");
    const fitButton = document.getElementById("chart-fit");
    const yZoomInButton = document.getElementById("chart-y-in");
    const yZoomOutButton = document.getElementById("chart-y-out");
    const yAutoButton = document.getElementById("chart-y-auto");
    const exportButton = document.getElementById("chart-export");
    const mplfinanceButton = document.getElementById("chart-mplfinance");
    const backtestButton = document.getElementById("chart-backtest");
    const summaryButton = document.getElementById("chart-summary-button");
    const fullscreenButton = document.getElementById("chart-fullscreen");
    const helpButton = document.getElementById("chart-help-button");
    const helpDialog = document.getElementById("chart-help");
    const closeHelpButton = document.getElementById("close-help");
    const showAllButton = document.getElementById("show-all-layers");
    const focusButton = document.getElementById("focus-price");
    const layerToggles = [...document.querySelectorAll("[data-layer]")];
    const labelsToggle = document.getElementById("toggle-labels");
    const gridToggle = document.getElementById("toggle-grid");
    const crosshairToggle = document.getElementById("toggle-crosshair");
    const sliderToggle = document.getElementById("toggle-slider");
    const liveBadge = document.getElementById("live-badge");

    if (!plot || !shell) return;

    const fullRange = ["__FIRST_TIME__", "__RANGE_END__"];
    const latestRange = ["__LATEST_START__", "__RANGE_END__"];
    const lastTimestamp = new Date("__LAST_TIME__");
    const originalText = plot.data.map((trace) => trace.text);
    const layerDefinitions = {
        fvg: ["bullish_fvg", "bearish_fvg"],
        orderblocks: ["bullish_ob", "bearish_ob"],
        liquidity: [
            "buy_side_liquidity",
            "sell_side_liquidity",
            "liquidity_sweeps"
        ],
        structure: [
            "structure_Bullish BOS",
            "structure_Bearish BOS",
            "structure_Bullish CHoCH",
            "structure_Bearish CHoCH"
        ],
        swings: ["swings"],
        trendmap: ["trendmap"],
        levels: ["previous_4h"],
        sessions: ["session_London", "session_NewYork"],
        retracements: ["retracements"]
    };
    let compactMode = null;
    let resizeFrame = null;

    function applyResponsiveLayout() {
        const isCompact = window.matchMedia("(max-width: 760px)").matches;

        Plotly.Plots.resize(plot);

        if (compactMode === isCompact) return;
        compactMode = isCompact;

        Plotly.relayout(plot, {
            margin: isCompact
                ? {l: 52, r: 72, t: 178, b: 58}
                : {l: 76, r: 132, t: 142, b: 76},
            "font.size": isCompact ? 11 : 13,
            "title.font.size": isCompact ? 15 : 18,
            "xaxis.title.text": isCompact ? "" : "Time",
            "yaxis.title.text": isCompact ? "" : "Gold Price"
        });
    }

    function scheduleResize() {
        if (resizeFrame !== null) cancelAnimationFrame(resizeFrame);
        resizeFrame = requestAnimationFrame(() => {
            resizeFrame = null;
            applyResponsiveLayout();
        });
    }

    function scaleYAxis(factor) {
        const range = plot?._fullLayout?.yaxis?.range;

        if (!range || range.length !== 2) return;

        const low = Number(range[0]);
        const high = Number(range[1]);

        if (!Number.isFinite(low) || !Number.isFinite(high)) return;

        const center = (low + high) / 2;
        const halfRange = Math.max(
            ((high - low) / 2) * factor,
            0.01
        );

        Plotly.relayout(plot, {
            "yaxis.autorange": false,
            "yaxis.range": [
                center - halfRange,
                center + halfRange
            ]
        });
    }

    function setTimeWindow(days) {
        const start = new Date(
            lastTimestamp.getTime() - days * 86400000
        );

        setChartWindow([start.toISOString(), fullRange[1]]);
    }

    function candlePriceRange(xRange) {
        const candles = plot.data.find(
            (trace) => trace.type === "candlestick"
        );

        if (!candles || !candles.x?.length) return null;

        const start = new Date(xRange[0]).getTime();
        const end = new Date(xRange[1]).getTime();
        const lows = [];
        const highs = [];

        candles.x.forEach((value, index) => {
            const timestamp = new Date(value).getTime();

            if (timestamp < start || timestamp > end) return;

            const low = Number(candles.low[index]);
            const high = Number(candles.high[index]);

            if (Number.isFinite(low) && Number.isFinite(high)) {
                lows.push(low);
                highs.push(high);
            }
        });

        if (!lows.length) return null;

        const low = Math.min(...lows);
        const high = Math.max(...highs);
        const span = Math.max(high - low, high * 0.002);
        const padding = span * 0.08;

        return [low - padding, high + padding];
    }

    function setChartWindow(xRange) {
        const priceRange = candlePriceRange(xRange);
        const updates = {
            "xaxis.range": xRange
        };

        if (priceRange) {
            updates["yaxis.autorange"] = false;
            updates["yaxis.range"] = priceRange;
        }

        Plotly.relayout(plot, updates);
    }

    function fitVisibleCandles() {
        const xRange = plot?._fullLayout?.xaxis?.range || fullRange;
        const priceRange = candlePriceRange(xRange);

        if (!priceRange) return;

        Plotly.relayout(plot, {
            "yaxis.autorange": false,
            "yaxis.range": priceRange
        });
    }

    function setLayerVisibility(layerName, visible) {
        const groups = layerDefinitions[layerName] || [];
        const traceIndices = [];

        plot.data.forEach((trace, index) => {
            if (groups.includes(trace.legendgroup)) {
                traceIndices.push(index);
            }
        });

        if (traceIndices.length) {
            Plotly.restyle(
                plot,
                {visible: visible ? true : "legendonly"},
                traceIndices
            );
        }

        const shapeUpdates = {};

        (plot.layout.shapes || []).forEach((shape, index) => {
            if (groups.includes(shape.legendgroup)) {
                shapeUpdates[`shapes[${index}].visible`] = visible;
            }
        });

        if (Object.keys(shapeUpdates).length) {
            Plotly.relayout(plot, shapeUpdates);
        }
    }

    function setAllLayers(visible) {
        layerToggles.forEach((toggle) => {
            toggle.checked = visible;
            setLayerVisibility(toggle.dataset.layer, visible);
        });
    }

    function setLabelsVisibility(visible) {
        plot.data.forEach((trace, index) => {
            if (originalText[index] === undefined) return;

            const replacement = visible
                ? originalText[index]
                : Array.isArray(originalText[index])
                ? originalText[index].map(() => null)
                : null;

            Plotly.restyle(
                plot,
                {text: [replacement]},
                [index]
            );
        });
    }

    function toggleIndicatorPanel(forceOpen) {
        const shouldOpen = forceOpen ?? indicatorPanel.hidden;
        indicatorPanel.hidden = !shouldOpen;
        indicatorButton?.setAttribute(
            "aria-expanded",
            String(shouldOpen)
        );
    }

    function saveChartView() {
        try {
            sessionStorage.setItem(
                "smc-chart-view",
                JSON.stringify({
                    xRange: plot?._fullLayout?.xaxis?.range,
                    yRange: plot?._fullLayout?.yaxis?.range
                })
            );
        } catch (_) {
            // Storage can be unavailable in privacy-restricted browsers.
        }
    }

    function restoreChartView() {
        try {
            const saved = JSON.parse(
                sessionStorage.getItem("smc-chart-view") || "null"
            );

            sessionStorage.removeItem("smc-chart-view");

            if (!saved) return;

            const update = {};

            if (saved.xRange?.length === 2) {
                update["xaxis.range"] = saved.xRange;
            }

            if (saved.yRange?.length === 2) {
                update["yaxis.range"] = saved.yRange;
                update["yaxis.autorange"] = false;
            }

            if (Object.keys(update).length) {
                Plotly.relayout(plot, update);
            }
        } catch (_) {
            // Ignore invalid or unavailable session storage.
        }
    }

    let seenLiveVersion = null;

    async function pollLiveStatus() {
        if (location.protocol === "file:") {
            if (liveBadge) {
                liveBadge.dataset.state = "static";
                liveBadge.textContent = "Static snapshot";
            }
            return;
        }

        try {
            const response = await fetch(
                "/api/status",
                {cache: "no-store"}
            );

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const status = await response.json();

            if (liveBadge) {
                liveBadge.dataset.state = status.error ? "error" : "live";
                liveBadge.textContent = status.error
                    ? `Live warning · ${status.error}`
                    : `Live · ${status.last_candle_time || "waiting"}`;
            }

            if (
                status.last_price !== null
                && status.last_price !== undefined
            ) {
                const livePrice = document.getElementById(
                    "live-current-price"
                );

                if (livePrice) {
                    livePrice.textContent = Number(
                        status.last_price
                    ).toLocaleString(undefined, {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2
                    });
                }
            }

            if (seenLiveVersion === null) {
                seenLiveVersion = status.version;
            } else if (status.version !== seenLiveVersion) {
                saveChartView();
                location.reload();
            }
        } catch (_) {
            if (liveBadge) {
                liveBadge.dataset.state = "error";
                liveBadge.textContent = "Live disconnected";
            }
        }
    }

    indicatorButton?.addEventListener("click", () => {
        toggleIndicatorPanel();
    });

    closePanelButton?.addEventListener("click", () => {
        toggleIndicatorPanel(false);
    });

    oneDayButton?.addEventListener("click", () => {
        setTimeWindow(1);
    });

    latestButton?.addEventListener("click", () => {
        setChartWindow(latestRange);
    });

    oneWeekButton?.addEventListener("click", () => {
        setTimeWindow(7);
    });

    fitButton?.addEventListener("click", () => {
        setChartWindow(fullRange);
    });

    yZoomInButton?.addEventListener("click", () => {
        scaleYAxis(0.78);
    });

    yZoomOutButton?.addEventListener("click", () => {
        scaleYAxis(1.28);
    });

    yAutoButton?.addEventListener("click", () => {
        fitVisibleCandles();
    });

    layerToggles.forEach((toggle) => {
        toggle.addEventListener("change", () => {
            setLayerVisibility(
                toggle.dataset.layer,
                toggle.checked
            );
        });
    });

    showAllButton?.addEventListener("click", () => {
        setAllLayers(true);
    });

    focusButton?.addEventListener("click", () => {
        setAllLayers(false);
    });

    labelsToggle?.addEventListener("change", () => {
        setLabelsVisibility(labelsToggle.checked);
    });

    gridToggle?.addEventListener("change", () => {
        Plotly.relayout(plot, {
            "xaxis.showgrid": gridToggle.checked,
            "yaxis.showgrid": gridToggle.checked
        });
    });

    crosshairToggle?.addEventListener("change", () => {
        Plotly.relayout(plot, {
            "xaxis.showspikes": crosshairToggle.checked,
            "yaxis.showspikes": crosshairToggle.checked
        });
    });

    sliderToggle?.addEventListener("change", () => {
        Plotly.relayout(plot, {
            "xaxis.rangeslider.visible": sliderToggle.checked
        });
        scheduleResize();
    });

    exportButton?.addEventListener("click", () => {
        Plotly.downloadImage(plot, {
            format: "png",
            filename: "xauusd_m15_smc_chart",
            width: 2200,
            height: 1200,
            scale: 2
        });
    });

    mplfinanceButton?.addEventListener("click", () => {
        const target = location.protocol === "file:"
            ? "xauusd_m15_smc_snapshot.png"
            : "/mplfinance";
        window.open(target, "_blank", "noopener");
    });

    backtestButton?.addEventListener("click", () => {
        const target = location.protocol === "file:"
            ? "xauusd_m15_smc_backtest.html"
            : "/backtest";
        window.open(target, "_blank", "noopener");
    });

    summaryButton?.addEventListener("click", () => {
        document.getElementById("chart-summary")?.scrollIntoView({
            behavior: "smooth",
            block: "start"
        });
    });

    helpButton?.addEventListener("click", () => {
        if (helpDialog && !helpDialog.open) {
            helpDialog.showModal();
        }
    });

    closeHelpButton?.addEventListener("click", () => {
        helpDialog?.close();
    });

    fullscreenButton?.addEventListener("click", async () => {
        if (!document.fullscreenElement) {
            await shell.requestFullscreen?.();
        } else {
            await document.exitFullscreen?.();
        }
        scheduleResize();
    });

    document.addEventListener("fullscreenchange", () => {
        fullscreenButton?.setAttribute(
            "aria-pressed",
            String(Boolean(document.fullscreenElement))
        );
        scheduleResize();
    });

    document.addEventListener("keydown", (event) => {
        if (event.target instanceof HTMLInputElement) return;

        if (event.key.toLowerCase() === "l") latestButton?.click();
        if (event.key.toLowerCase() === "r") fitButton?.click();
        if (event.key.toLowerCase() === "f") fullscreenButton?.click();
        if (event.key.toLowerCase() === "i") indicatorButton?.click();
        if (event.key.toLowerCase() === "e") exportButton?.click();
        if (event.key.toLowerCase() === "p") mplfinanceButton?.click();
        if (event.key.toLowerCase() === "b") backtestButton?.click();
        if (event.key.toLowerCase() === "s") summaryButton?.click();
        if (event.key.toLowerCase() === "h") helpButton?.click();
        if (event.key === "1") oneDayButton?.click();
        if (event.key === "3") latestButton?.click();
        if (event.key === "7") oneWeekButton?.click();
        if (event.key === "+" || event.key === "=") yZoomInButton?.click();
        if (event.key === "-" || event.key === "_") yZoomOutButton?.click();
        if (event.key === "0") yAutoButton?.click();
        if (event.key === "Escape") toggleIndicatorPanel(false);
    });

    window.addEventListener("resize", scheduleResize, {passive: true});

    if ("ResizeObserver" in window) {
        new ResizeObserver(scheduleResize).observe(shell);
    }

    applyResponsiveLayout();
    setLabelsVisibility(labelsToggle?.checked ?? false);
    restoreChartView();
    loadingOverlay?.setAttribute("hidden", "");
    previewImage?.setAttribute("hidden", "");
    pollLiveStatus();
    window.setInterval(
        pollLiveStatus,
        __LIVE_REFRESH_MS__
    );
})();
"""

    responsive_script = (
        responsive_script
        .replace(
            "__FIRST_TIME__",
            first_time.isoformat(),
        )
        .replace(
            "__LATEST_START__",
            latest_window_start.isoformat(),
        )
        .replace(
            "__RANGE_END__",
            chart_range_end.isoformat(),
        )
        .replace(
            "__LAST_TIME__",
            last_time.isoformat(),
        )
        .replace(
            "__LIVE_REFRESH_MS__",
            str(LIVE_REFRESH_SECONDS * 1000),
        )
    )

    ensure_plotly_js_asset()

    html = fig.to_html(
        include_plotlyjs=False,
        full_html=True,
        config=chart_config,
        div_id="smc-chart",
        post_script=responsive_script,
    )
    html = defer_plotly_initialization(
        html
    )

    plotly_loader_script = f"""
<script>
    window.addEventListener("DOMContentLoaded", () => {{
        const script = document.createElement("script");
        script.src = "{PLOTLY_JS_FILE}";
        script.onload = () => window.dispatchEvent(
            new Event("smc-plotly-ready")
        );
        script.onerror = () => {{
            const loading = document.getElementById("chart-loading");
            const message = loading?.querySelector("strong");
            const detail = loading?.querySelector("small");
            const badge = document.getElementById("live-badge");

            if (message) message.textContent = "Chart engine could not load";
            if (detail) detail.textContent = "Restart the launcher and refresh this page.";
            if (badge) {{
                badge.dataset.state = "error";
                badge.textContent = "Chart unavailable";
            }}
        }};
        document.head.appendChild(script);
    }});
</script>
"""

    responsive_styles = """
<style>
    :root {
        color-scheme: dark;
        font-family: Arial, sans-serif;
        background: #101215;
    }

    * {
        box-sizing: border-box;
    }

    html,
    body {
        width: 100%;
        height: 100%;
        margin: 0;
        overflow-x: hidden;
        overflow-y: auto;
        scroll-behavior: smooth;
        background: #101215;
    }

    #chart-shell {
        position: relative;
        width: 100%;
        height: 100vh;
        height: 100dvh;
        min-height: 520px;
        overflow: hidden;
        background: #101215;
    }

    #smc-chart {
        width: 100% !important;
        height: 100% !important;
        min-height: 520px;
    }

    .chart-loading {
        position: absolute;
        z-index: 4;
        inset: 300px 24px 72px;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-direction: column;
        gap: 10px;
        color: #dce6ee;
        text-align: center;
        pointer-events: none;
    }

    .chart-preview {
        position: absolute;
        z-index: 1;
        inset: 285px 0 54px;
        width: 100%;
        height: calc(100% - 339px);
        object-fit: contain;
        object-position: center;
        opacity: .62;
        pointer-events: none;
    }

    .chart-preview[hidden] {
        display: none;
    }

    .chart-loading[hidden] {
        display: none;
    }

    .chart-loading-spinner {
        width: 30px;
        height: 30px;
        border: 3px solid #34404a;
        border-top-color: #35d4b2;
        border-radius: 50%;
        animation: chart-loading-spin .8s linear infinite;
    }

    .chart-loading strong {
        font-size: 14px;
    }

    .chart-loading small {
        color: #8f9ca8;
        font-size: 11px;
    }

    @keyframes chart-loading-spin {
        to { transform: rotate(360deg); }
    }

    .chart-actions {
        position: absolute;
        z-index: 20;
        top: max(52px, env(safe-area-inset-top));
        left: max(8px, env(safe-area-inset-left));
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        max-width: min(760px, calc(100vw - 120px));
        padding: 5px;
        border: 1px solid #39424d;
        border-radius: 9px;
        background: rgba(16, 18, 21, 0.88);
        box-shadow: 0 4px 18px rgba(0, 0, 0, 0.28);
        backdrop-filter: blur(8px);
    }

    .toolbar-group {
        display: flex;
        align-items: center;
        gap: 4px;
    }

    .toolbar-group + .toolbar-group {
        padding-left: 6px;
        border-left: 1px solid #3b4652;
    }

    .chart-actions button {
        min-height: 34px;
        padding: 6px 11px;
        border: 1px solid #505b68;
        border-radius: 6px;
        color: #eef3f8;
        background: #20262d;
        font: inherit;
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        touch-action: manipulation;
    }

    .chart-actions button:hover,
    .chart-actions button:focus-visible {
        border-color: #19c9a5;
        background: #29323a;
        outline: none;
    }

    .chart-actions button:active {
        transform: translateY(1px);
    }

    .chart-actions button[aria-expanded="true"],
    .chart-actions .primary-action {
        border-color: #19c9a5;
        color: #dffff7;
        background: #183a35;
    }

    .indicator-panel {
        position: absolute;
        z-index: 30;
        top: 102px;
        left: max(8px, env(safe-area-inset-left));
        width: 282px;
        max-height: calc(100dvh - 118px);
        overflow: auto;
        padding: 12px;
        border: 1px solid #3d4955;
        border-radius: 11px;
        color: #edf3f8;
        background: rgba(17, 20, 24, 0.96);
        box-shadow: 0 12px 36px rgba(0, 0, 0, 0.42);
        backdrop-filter: blur(12px);
    }

    .indicator-panel[hidden] {
        display: none;
    }

    .panel-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 10px;
    }

    .panel-header h2 {
        margin: 0;
        font-size: 14px;
        letter-spacing: 0.02em;
    }

    .icon-button {
        width: 30px;
        height: 30px;
        padding: 0;
        border: 1px solid #4a5663;
        border-radius: 6px;
        color: #dbe4ec;
        background: #252b32;
        cursor: pointer;
    }

    .panel-actions {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 6px;
        margin-bottom: 12px;
    }

    .panel-hint {
        margin: -4px 2px 12px;
        color: #8fa0af;
        font-size: 10px;
        line-height: 1.45;
    }

    .panel-actions button {
        min-height: 32px;
        border: 1px solid #485461;
        border-radius: 6px;
        color: #e9f0f6;
        background: #232a31;
        cursor: pointer;
    }

    .indicator-panel fieldset {
        margin: 0 0 12px;
        padding: 8px;
        border: 1px solid #303a44;
        border-radius: 8px;
    }

    .indicator-panel legend {
        padding: 0 6px;
        color: #98a8b7;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.09em;
        text-transform: uppercase;
    }

    .layer-option,
    .display-option {
        display: grid;
        grid-template-columns: auto 1fr auto;
        align-items: center;
        gap: 8px;
        min-height: 34px;
        padding: 4px 3px;
        color: #dce5ed;
        font-size: 12px;
        cursor: pointer;
    }

    .display-option {
        grid-template-columns: 1fr auto;
    }

    .layer-option + .layer-option,
    .display-option + .display-option {
        border-top: 1px solid rgba(67, 79, 91, 0.45);
    }

    .layer-option input,
    .display-option input {
        width: 16px;
        height: 16px;
        margin: 0;
        accent-color: #19c9a5;
    }

    .layer-swatch {
        width: 18px;
        height: 4px;
        border-radius: 4px;
        background: var(--swatch, #aab5c0);
        box-shadow: 0 0 8px color-mix(in srgb, var(--swatch), transparent 45%);
    }

    .chart-help {
        width: min(540px, calc(100vw - 24px));
        padding: 0;
        border: 1px solid #465360;
        border-radius: 12px;
        color: #eaf1f7;
        background: #15191e;
        box-shadow: 0 18px 60px rgba(0, 0, 0, 0.58);
    }

    .chart-help::backdrop {
        background: rgba(4, 6, 8, 0.72);
        backdrop-filter: blur(3px);
    }

    .help-content {
        padding: 18px;
    }

    .help-content h2 {
        margin: 0 0 12px;
        font-size: 18px;
    }

    .help-content h3 {
        margin: 16px 0 6px;
        color: #8fe8d4;
        font-size: 12px;
        text-transform: uppercase;
    }

    .help-content p,
    .help-content li {
        color: #bdc9d4;
        font-size: 12px;
        line-height: 1.55;
    }

    .help-content ul {
        margin: 6px 0;
        padding-left: 20px;
    }

    .help-footer {
        display: flex;
        justify-content: flex-end;
        margin-top: 16px;
    }

    .help-footer button {
        min-height: 34px;
        padding: 6px 14px;
        border: 1px solid #19c9a5;
        border-radius: 6px;
        color: #dffff7;
        background: #183a35;
        cursor: pointer;
    }

    .chart-status {
        position: absolute;
        z-index: 18;
        right: max(12px, env(safe-area-inset-right));
        bottom: 64px;
        max-width: min(440px, calc(100vw - 24px));
        padding: 7px 10px;
        border: 1px solid #495563;
        border-radius: 7px;
        color: #e8eef5;
        background: rgba(16, 18, 21, 0.86);
        box-shadow: 0 3px 14px rgba(0, 0, 0, 0.24);
        font-size: 12px;
        pointer-events: none;
        backdrop-filter: blur(7px);
    }

    .live-badge {
        position: absolute;
        z-index: 21;
        top: max(10px, env(safe-area-inset-top));
        left: max(10px, env(safe-area-inset-left));
        display: inline-flex;
        align-items: center;
        gap: 7px;
        min-height: 28px;
        padding: 5px 9px;
        border: 1px solid #3d4955;
        border-radius: 999px;
        color: #a9b7c4;
        background: rgba(16, 18, 21, 0.9);
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
    }

    .live-badge::before {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #7f8b97;
        content: "";
    }

    .live-badge[data-state="live"] {
        border-color: rgba(25, 201, 165, 0.55);
        color: #9ff3df;
    }

    .live-badge[data-state="live"]::before {
        background: #19c9a5;
        box-shadow: 0 0 9px rgba(25, 201, 165, 0.9);
    }

    .live-badge[data-state="error"] {
        border-color: rgba(255, 77, 91, 0.65);
        color: #ff9aa4;
    }

    .live-badge[data-state="error"]::before {
        background: #ff4d5b;
    }

    .analysis-dashboard {
        width: min(1440px, 100%);
        margin: 0 auto;
        padding: 44px clamp(16px, 4vw, 56px) 64px;
        color: #e8eef4;
        background:
            radial-gradient(circle at 12% 0%, rgba(25, 201, 165, 0.08), transparent 28%),
            #0d1013;
    }

    .backtest-dashboard {
        width: min(1440px, 100%);
        margin: 0 auto;
        padding: 0 clamp(16px, 4vw, 56px) 64px;
        color: #e8eef4;
        background: #0d1013;
    }

    .backtest-links {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
    }

    .backtest-metrics {
        margin-bottom: 22px;
    }

    .trade-table-wrap {
        width: 100%;
        overflow-x: auto;
        border: 1px solid #27313a;
        border-radius: 10px;
        background: #11161a;
    }

    .trade-table {
        width: 100%;
        min-width: 820px;
        border-collapse: collapse;
        font-size: 11px;
    }

    .trade-table caption {
        padding: 14px 16px;
        color: #dce6ee;
        font-size: 13px;
        font-weight: 700;
        text-align: left;
    }

    .trade-table th,
    .trade-table td {
        padding: 10px 12px;
        border-top: 1px solid #26313a;
        color: #aab7c2;
        text-align: left;
        white-space: nowrap;
    }

    .trade-table th {
        color: #dce6ee;
        background: #171d22;
    }

    .trade-table .trade-win {
        color: #65e9cb;
    }

    .trade-table .trade-loss {
        color: #ff8c97;
    }

    .dashboard-header,
    .reference-header {
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        gap: 24px;
        margin-bottom: 22px;
    }

    .dashboard-header h2,
    .reference-header h2 {
        margin: 2px 0 5px;
        font-size: clamp(22px, 3vw, 34px);
    }

    .dashboard-header p,
    .reference-header p {
        max-width: 760px;
        margin: 0;
        color: #91a0ae;
        font-size: 12px;
        line-height: 1.55;
    }

    .eyebrow {
        color: #19c9a5 !important;
        font-size: 10px !important;
        font-weight: 800;
        letter-spacing: 0.15em;
        text-transform: uppercase;
    }

    .back-to-chart {
        flex: 0 0 auto;
        padding: 8px 12px;
        border: 1px solid #40505d;
        border-radius: 7px;
        color: #dbe6ef;
        text-decoration: none;
        background: #171c21;
    }

    .metric-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
        margin-bottom: 52px;
    }

    .metric-card {
        min-width: 0;
        padding: 16px;
        border: 1px solid #27313a;
        border-radius: 10px;
        background: linear-gradient(145deg, #151a1f, #11151a);
        box-shadow: 0 8px 22px rgba(0, 0, 0, 0.18);
    }

    .metric-card > span {
        display: block;
        margin-bottom: 8px;
        color: #8393a1;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }

    .metric-card strong {
        display: block;
        overflow-wrap: anywhere;
        color: #f0f5f9;
        font-size: 16px;
        line-height: 1.35;
    }

    .metric-card small {
        display: block;
        margin-top: 7px;
        color: #9aa8b5;
        font-size: 11px;
        line-height: 1.45;
    }

    .metric-primary {
        border-color: rgba(25, 201, 165, 0.45);
    }

    .metric-primary strong {
        color: #65e9cb;
        font-size: 24px;
    }

    .metric-wide {
        grid-column: span 2;
    }

    .reference-header {
        display: block;
    }

    .definition-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
    }

    .definition-card {
        --accent: #8393a1;
        padding: 18px;
        border: 1px solid #26313a;
        border-top: 3px solid var(--accent);
        border-radius: 9px;
        background: #12171b;
    }

    .definition-card h3 {
        margin: 0 0 12px;
        color: #f0f4f7;
        font-size: 15px;
    }

    .definition-card h3 span {
        color: var(--accent);
        font-size: 10px;
        letter-spacing: 0.08em;
    }

    .definition-card p {
        margin: 7px 0 0;
        color: #a5b2bd;
        font-size: 12px;
        line-height: 1.58;
    }

    .definition-card strong {
        color: #dce5ec;
    }

    .fvg-definition { --accent: #00dc79; }
    .ob-definition { --accent: #5d9cff; }
    .liquidity-definition { --accent: #d95cff; }
    .structure-definition { --accent: #00e5ff; }
    .structure-map-definition { --accent: #7bdcff; }
    .dealing-range-definition { --accent: #8fe8a6; }
    .swing-definition { --accent: #ff6978; }
    .levels-definition { --accent: #a9b4bf; }
    .sessions-definition { --accent: #ff9b2d; }
    .retracement-definition { --accent: #e1e8f0; }

    .analysis-disclaimer {
        margin: 24px 0 0;
        padding: 12px 14px;
        border-left: 3px solid #ffb300;
        color: #9daab5;
        background: rgba(255, 179, 0, 0.06);
        font-size: 11px;
        line-height: 1.5;
    }

    @media (max-width: 760px) {
        #chart-shell,
        #smc-chart {
            min-height: 460px;
        }

        .chart-actions {
            gap: 4px;
            padding: 4px;
            max-width: calc(100vw - 78px);
        }

        .toolbar-group + .toolbar-group {
            padding-left: 4px;
        }

        .chart-actions button {
            min-height: 38px;
            padding: 7px 10px;
            font-size: 11px;
        }

        .chart-status {
            right: 8px;
            bottom: 54px;
            padding: 5px 7px;
            font-size: 10px;
        }

        .metric-grid,
        .definition-grid {
            grid-template-columns: 1fr;
        }

        .metric-wide {
            grid-column: auto;
        }

        .dashboard-header {
            align-items: flex-start;
            flex-direction: column;
        }

        .analysis-dashboard {
            padding-top: 30px;
        }

        .backtest-dashboard {
            padding-bottom: 40px;
        }

        .indicator-panel {
            top: 148px;
            left: 8px;
            width: calc(100vw - 16px);
            max-height: calc(100dvh - 158px);
        }

        .modebar {
            transform: scale(0.9);
            transform-origin: top right;
        }
    }

    @media (prefers-reduced-motion: reduce) {
        .chart-actions button:active {
            transform: none;
        }

        .chart-loading-spinner {
            animation: none;
        }
    }
</style>
"""

    chart_controls = """
<main id="chart-shell">
    <div id="live-badge" class="live-badge" data-state="static">Loading chart</div>
    <nav class="chart-actions" aria-label="Chart controls">
        <span class="toolbar-group">
            <button id="chart-indicators" class="primary-action" type="button" title="Organize indicators (I)" aria-expanded="false" aria-controls="indicator-panel">
                Indicators
            </button>
        </span>
        <span class="toolbar-group" aria-label="Time range">
            <button id="chart-1d" type="button" title="Latest day (1)">1D</button>
            <button id="chart-latest" type="button" title="Latest three days (3 or L)">3D</button>
            <button id="chart-1w" type="button" title="Latest week (7)">1W</button>
            <button id="chart-fit" type="button" title="All loaded candles (R)">All</button>
        </span>
        <span class="toolbar-group" aria-label="Vertical price scale">
            <button id="chart-y-in" type="button" title="Zoom in vertically (+)">Y+</button>
            <button id="chart-y-out" type="button" title="Zoom out vertically (-)">Y-</button>
            <button id="chart-y-auto" type="button" title="Automatically fit the vertical price scale (0)">Y Auto</button>
        </span>
        <span class="toolbar-group">
            <button id="chart-summary-button" type="button" title="Open summary and indicator guide (S)">Summary</button>
            <button id="chart-export" type="button" title="Export a PNG image (E)">Export</button>
            <button id="chart-mplfinance" type="button" title="Open the clean mplfinance chart (P)">MPL View</button>
            <button id="chart-backtest" type="button" title="Open interactive strategy backtest (B)">Backtest</button>
            <button id="chart-fullscreen" type="button" title="Toggle fullscreen (F)" aria-pressed="false">Fullscreen</button>
            <button id="chart-help-button" type="button" title="Chart help and shortcuts (H)">Help</button>
        </span>
    </nav>

    <img id="chart-preview" class="chart-preview" src="__MPLFINANCE_OUTPUT_FILE__" alt="Latest generated XAUUSD chart preview">

    <div id="chart-loading" class="chart-loading" role="status" aria-live="polite">
        <span class="chart-loading-spinner" aria-hidden="true"></span>
        <strong>Loading XAUUSD chart...</strong>
        <small>The first load prepares the local chart engine.</small>
    </div>

    <aside id="indicator-panel" class="indicator-panel" aria-label="Indicator controls" hidden>
        <header class="panel-header">
            <h2>Indicator layers</h2>
            <button id="close-indicators" class="icon-button" type="button" aria-label="Close indicator panel">×</button>
        </header>

        <div class="panel-actions">
            <button id="show-all-layers" type="button">Show all</button>
            <button id="focus-price" type="button">Price only</button>
        </div>

        <p class="panel-hint">
            Checked means enabled. Signals only appear where detected; use All and Y Auto when a layer is outside the current view.
        </p>

        <fieldset>
            <legend>Smart Money Concepts</legend>
            <label class="layer-option">
                <span class="layer-swatch" style="--swatch:#00dc79"></span>
                <span>Fair Value Gaps</span>
                <input type="checkbox" data-layer="fvg" checked>
            </label>
            <label class="layer-option">
                <span class="layer-swatch" style="--swatch:#5d9cff"></span>
                <span>Order Blocks</span>
                <input type="checkbox" data-layer="orderblocks" checked>
            </label>
            <label class="layer-option">
                <span class="layer-swatch" style="--swatch:#d95cff"></span>
                <span>Liquidity &amp; Sweeps</span>
                <input type="checkbox" data-layer="liquidity" checked>
            </label>
            <label class="layer-option">
                <span class="layer-swatch" style="--swatch:#00e5ff"></span>
                <span>BOS &amp; CHoCH</span>
                <input type="checkbox" data-layer="structure" checked>
            </label>
            <label class="layer-option">
                <span class="layer-swatch" style="--swatch:#ff6978"></span>
                <span>Swing Structure</span>
                <input type="checkbox" data-layer="swings" checked>
            </label>
            <label class="layer-option">
                <span class="layer-swatch" style="--swatch:#7bdcff"></span>
                <span>Structure Map &amp; Range</span>
                <input type="checkbox" data-layer="trendmap" checked>
            </label>
            <label class="layer-option">
                <span class="layer-swatch" style="--swatch:#a9b4bf"></span>
                <span>Previous 4H Levels</span>
                <input type="checkbox" data-layer="levels" checked>
            </label>
            <label class="layer-option">
                <span class="layer-swatch" style="--swatch:#ff9b2d"></span>
                <span>Trading Sessions</span>
                <input type="checkbox" data-layer="sessions" checked>
            </label>
            <label class="layer-option">
                <span class="layer-swatch" style="--swatch:#e1e8f0"></span>
                <span>Retracement Turns</span>
                <input type="checkbox" data-layer="retracements" checked>
            </label>
        </fieldset>

        <fieldset>
            <legend>Display</legend>
            <label class="display-option">
                <span>Indicator labels</span>
                <input id="toggle-labels" type="checkbox">
            </label>
            <label class="display-option">
                <span>Grid lines</span>
                <input id="toggle-grid" type="checkbox" checked>
            </label>
            <label class="display-option">
                <span>Crosshair guides</span>
                <input id="toggle-crosshair" type="checkbox" checked>
            </label>
            <label class="display-option">
                <span>Range slider</span>
                <input id="toggle-slider" type="checkbox" checked>
            </label>
        </fieldset>
    </aside>

    <aside class="chart-status" aria-live="polite">
        __STATUS_TEXT__
    </aside>

    <dialog id="chart-help" class="chart-help">
        <div class="help-content">
            <h2>Chart controls</h2>
            <p>Drag the chart to move through time. Use the mouse wheel or trackpad to zoom. Hover or tap an indicator for exact values.</p>
            <h3>Price scale</h3>
            <ul>
                <li><strong>Y+</strong> and <strong>Y-</strong> adjust vertical scale.</li>
                <li><strong>Y Auto</strong> fits visible prices automatically.</li>
                <li>Drag directly on an axis for manual scaling.</li>
            </ul>
            <h3>Keyboard shortcuts</h3>
            <p>1/3/7: time range · R: all · +/-: vertical zoom · 0: Y Auto · I: indicators · S: summary · E: export · P: mplfinance view · B: backtest · F: fullscreen · H: help</p>
            <h3>Indicator abbreviations</h3>
            <p>FVG: Fair Value Gap · OB: Order Block · BSL/SSL: buy-side/sell-side liquidity · HH/HL/LH/LL: swing sequence · EQ: dealing-range midpoint · PH/PL: previous high/low · C/D: current/deepest retracement.</p>
            <div class="help-footer">
                <button id="close-help" type="button">Done</button>
            </div>
        </div>
    </dialog>
"""

    chart_controls = (
        chart_controls
        .replace(
            "__STATUS_TEXT__",
            status_text,
        )
        .replace(
            "__MPLFINANCE_OUTPUT_FILE__",
            MPLFINANCE_OUTPUT_FILE,
        )
    )

    dashboard_html = build_analysis_dashboard(
        data,
        backtest_summary=backtest_summary,
    )

    html = html.replace(
        "</head>",
        (
            f"{plotly_loader_script}{responsive_styles}</head>"
        ),
    )

    html = html.replace(
        "<body>",
        f"<body>{chart_controls}",
        1,
    )

    html = html.replace(
        "</body>",
        f"</main>{dashboard_html}</body>",
        1,
    )

    temporary_output_path = output_path.with_suffix(
        ".tmp"
    )

    temporary_output_path.write_text(
        html,
        encoding="utf-8",
    )

    try:
        temporary_output_path.replace(
            output_path
        )
    except PermissionError:
        output_path.write_text(
            html,
            encoding="utf-8",
        )
        temporary_output_path.unlink(
            missing_ok=True
        )

    return output_path


# =========================================================
# LIVE LOCAL DASHBOARD
# =========================================================

def get_mt5_live_price(
    fallback_price: float,
) -> float:
    """Return the best available live MT5 price."""

    tick = mt5.symbol_info_tick(SYMBOL)

    if tick is None:
        return float(fallback_price)

    for attribute in ["last", "bid", "ask"]:
        value = float(
            getattr(tick, attribute, 0) or 0
        )

        if value > 0:
            return value

    return float(fallback_price)


def update_live_state(
    *,
    last_candle_time=None,
    last_price=None,
    error=None,
    increment_version: bool = False,
) -> None:
    """Update state returned to the dashboard polling endpoint."""

    with LIVE_STATE_LOCK:
        if increment_version:
            LIVE_STATE["version"] += 1

        if last_candle_time is not None:
            LIVE_STATE["last_candle_time"] = str(
                last_candle_time
            )

        if last_price is not None:
            LIVE_STATE["last_price"] = float(
                last_price
            )

        LIVE_STATE["updated_at"] = (
            pd.Timestamp.now(tz="UTC").isoformat()
        )
        LIVE_STATE["error"] = error


def analyze_and_write_outputs(
    candles: pd.DataFrame,
) -> tuple[pd.DataFrame, Path, Path]:
    """Calculate all indicators and atomically refresh outputs."""

    results = calculate_smc_indicators(
        candles
    )

    csv_path = Path(
        CSV_OUTPUT_FILE
    ).resolve()

    temporary_csv_path = csv_path.with_suffix(
        ".tmp"
    )

    results.to_csv(
        temporary_csv_path,
        index=False,
    )

    try:
        temporary_csv_path.replace(
            csv_path
        )
    except PermissionError:
        results.to_csv(
            csv_path,
            index=False,
        )
        temporary_csv_path.unlink(
            missing_ok=True
        )

    backtest_summary = None

    try:
        backtest_summary = run_smc_backtest(
            results,
            output_file=BACKTEST_OUTPUT_FILE,
            trades_file=BACKTEST_TRADES_FILE,
            swing_confirmation_bars=SWING_LENGTH,
            cash=BACKTEST_CASH,
            spread=BACKTEST_SPREAD,
            margin=BACKTEST_MARGIN,
            position_fraction=BACKTEST_POSITION_FRACTION,
            risk_reward=BACKTEST_RISK_REWARD,
            atr_multiplier=BACKTEST_ATR_MULTIPLIER,
        )
    except Exception as backtest_error:
        print(
            "Backtest warning: "
            f"{backtest_error}"
        )

    chart_path = create_interactive_chart(
        results=results,
        number_of_candles=CHART_CANDLES,
        backtest_summary=backtest_summary,
    )

    create_mplfinance_snapshot(
        results=results,
        number_of_candles=MPLFINANCE_CANDLES,
    )

    return results, csv_path, chart_path


def build_mplfinance_viewer() -> str:
    """Return a responsive live viewer for the mplfinance snapshot."""

    viewer = """<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>XAUUSD SMC clean chart</title>
    <style>
        :root { color-scheme: dark; font-family: Arial, sans-serif; background: #0d1013; }
        * { box-sizing: border-box; }
        body { margin: 0; color: #e8eef4; background: #0d1013; }
        header { position: sticky; z-index: 2; top: 0; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 10px 16px; border-bottom: 1px solid #29333d; background: rgba(13,16,19,.94); backdrop-filter: blur(8px); }
        h1 { margin: 0; font-size: 16px; }
        p { margin: 3px 0 0; color: #94a2af; font-size: 11px; }
        nav { display: flex; flex-wrap: wrap; gap: 7px; }
        a { padding: 7px 10px; border: 1px solid #465460; border-radius: 6px; color: #e8eef4; background: #1c2329; text-decoration: none; font-size: 12px; }
        main { padding: 12px; }
        img { display: block; width: 100%; height: auto; border: 1px solid #28323b; background: #101215; }
        #status[data-state="live"] { color: #65e9cb; }
        #status[data-state="error"] { color: #ff8c97; }
        @media (max-width: 640px) { header { align-items: flex-start; flex-direction: column; } main { padding: 6px; } }
    </style>
</head>
<body>
    <header>
        <div>
            <h1>XAUUSD M15 · mplfinance clean view</h1>
            <p id="status" data-state="live">Loading latest completed candle…</p>
        </div>
        <nav>
            <a href="/">Interactive dashboard</a>
            <a href="/mplfinance.png" download="xauusd_m15_smc_snapshot.png">Download PNG</a>
        </nav>
    </header>
    <main>
        <img id="snapshot" src="/mplfinance.png?v=0" alt="XAUUSD M15 candlestick chart with Smart Money Concepts overlays">
    </main>
    <script>
        const image = document.getElementById("snapshot");
        const status = document.getElementById("status");
        let seenVersion = null;

        async function refreshSnapshot() {
            try {
                const response = await fetch("/api/status", {cache: "no-store"});
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const live = await response.json();
                status.dataset.state = live.error ? "error" : "live";
                status.textContent = live.error
                    ? `Live warning · ${live.error}`
                    : `Live · completed candle ${live.last_candle_time || "waiting"}`;

                if (seenVersion === null || live.version !== seenVersion) {
                    seenVersion = live.version;
                    image.src = `/mplfinance.png?v=${encodeURIComponent(live.version)}`;
                }
            } catch (_) {
                status.dataset.state = "error";
                status.textContent = "Live dashboard disconnected";
            }
        }

        refreshSnapshot();
        window.setInterval(refreshSnapshot, __LIVE_REFRESH_MS__);
    </script>
</body>
</html>
"""

    return viewer.replace(
        "__LIVE_REFRESH_MS__",
        str(LIVE_REFRESH_SECONDS * 1000),
    )


class LiveDashboardRequestHandler(
    BaseHTTPRequestHandler
):
    """Serve the chart and its lightweight live status API."""

    def send_bytes(
        self,
        status_code: int,
        content_type: str,
        payload: bytes,
        cache_control: str = "no-store, no-cache, must-revalidate",
    ) -> None:
        self.send_response(status_code)
        self.send_header(
            "Content-Type",
            content_type,
        )
        self.send_header(
            "Content-Length",
            str(len(payload)),
        )
        self.send_header(
            "Cache-Control",
            cache_control,
        )
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        request_path = urlparse(
            self.path
        ).path

        if request_path == "/api/status":
            with LIVE_STATE_LOCK:
                payload = json.dumps(
                    LIVE_STATE
                ).encode("utf-8")

            self.send_bytes(
                200,
                "application/json; charset=utf-8",
                payload,
            )
            return

        if request_path in {"/", "/chart"}:
            chart_path = Path(
                HTML_OUTPUT_FILE
            ).resolve()

            if not chart_path.exists():
                self.send_bytes(
                    503,
                    "text/plain; charset=utf-8",
                    b"Chart is still being generated.",
                )
                return

            self.send_bytes(
                200,
                "text/html; charset=utf-8",
                chart_path.read_bytes(),
            )
            return

        if request_path == f"/{PLOTLY_JS_FILE}":
            plotly_path = Path(
                PLOTLY_JS_FILE
            ).resolve()

            if not plotly_path.exists():
                self.send_bytes(
                    503,
                    "text/plain; charset=utf-8",
                    b"The local Plotly runtime is still being generated.",
                )
                return

            self.send_bytes(
                200,
                "application/javascript; charset=utf-8",
                plotly_path.read_bytes(),
                cache_control="public, max-age=86400",
            )
            return

        if request_path == "/mplfinance":
            self.send_bytes(
                200,
                "text/html; charset=utf-8",
                build_mplfinance_viewer().encode("utf-8"),
            )
            return

        if request_path in {
            "/mplfinance.png",
            f"/{MPLFINANCE_OUTPUT_FILE}",
        }:
            snapshot_path = Path(
                MPLFINANCE_OUTPUT_FILE
            ).resolve()

            if not snapshot_path.exists():
                self.send_bytes(
                    503,
                    "text/plain; charset=utf-8",
                    b"The mplfinance chart is still being generated.",
                )
                return

            self.send_bytes(
                200,
                "image/png",
                snapshot_path.read_bytes(),
            )
            return

        if request_path in {
            "/backtest",
            f"/{BACKTEST_OUTPUT_FILE}",
        }:
            backtest_path = Path(
                BACKTEST_OUTPUT_FILE
            ).resolve()

            if not backtest_path.exists():
                self.send_bytes(
                    503,
                    "text/plain; charset=utf-8",
                    b"The backtest is still being generated.",
                )
                return

            self.send_bytes(
                200,
                "text/html; charset=utf-8",
                backtest_path.read_bytes(),
            )
            return

        if request_path in {
            "/backtest-trades.csv",
            f"/{BACKTEST_TRADES_FILE}",
        }:
            trades_path = Path(
                BACKTEST_TRADES_FILE
            ).resolve()

            if not trades_path.exists():
                self.send_bytes(
                    503,
                    "text/plain; charset=utf-8",
                    b"The backtest trade list is still being generated.",
                )
                return

            self.send_bytes(
                200,
                "text/csv; charset=utf-8",
                trades_path.read_bytes(),
            )
            return

        if request_path == "/favicon.ico":
            self.send_bytes(
                204,
                "image/x-icon",
                b"",
            )
            return

        self.send_bytes(
            404,
            "text/plain; charset=utf-8",
            b"Not found",
        )

    def log_message(
        self,
        format_string: str,
        *args,
    ) -> None:
        """Keep routine browser polling out of terminal output."""


def start_live_dashboard_server(
) -> tuple[ThreadingHTTPServer, str]:
    """Start a localhost server, using the next port if needed."""

    last_error = None

    for port in range(
        LIVE_PORT,
        LIVE_PORT + 10,
    ):
        try:
            server = ThreadingHTTPServer(
                (LIVE_HOST, port),
                LiveDashboardRequestHandler,
            )
            server.daemon_threads = True

            thread = threading.Thread(
                target=server.serve_forever,
                name="smc-live-dashboard",
                daemon=True,
            )
            thread.start()

            return (
                server,
                f"http://{LIVE_HOST}:{port}/",
            )
        except OSError as error:
            last_error = error

    raise RuntimeError(
        "Could not start the local dashboard server on "
        f"ports {LIVE_PORT}-{LIVE_PORT + 9}: {last_error}"
    )


# =========================================================
# TERMINAL SUMMARY
# =========================================================

def count_signals(
    results: pd.DataFrame,
    column: str,
) -> int:
    """Count actual non-zero signals."""

    if column not in results.columns:
        return 0

    return int(
        results[column]
        .fillna(0)
        .ne(0)
        .sum()
    )


def print_summary(
    results: pd.DataFrame,
) -> None:
    """Print a compact terminal summary."""

    latest = results.iloc[-1]

    print("\n========================================")
    print("LATEST XAUUSD DATA")
    print("========================================")

    print(f"Time:  {latest['time']}")
    print(f"Close: {latest['close']:.2f}")

    previous_high = latest_value(
        results,
        "Daily_PreviousHigh",
    )

    previous_low = latest_value(
        results,
        "Daily_PreviousLow",
    )

    if previous_high is not None:
        print(
            f"Previous daily high: "
            f"{float(previous_high):.2f}"
        )

    if previous_low is not None:
        print(
            f"Previous daily low:  "
            f"{float(previous_low):.2f}"
        )

    print("\n========================================")
    print("INDICATOR SUMMARY")
    print("========================================")

    print(
        f"Fair Value Gaps: "
        f"{count_signals(results, 'FVG_FVG')}"
    )

    print(
        f"BOS signals:     "
        f"{count_signals(results, 'Structure_BOS')}"
    )

    print(
        f"CHoCH signals:   "
        f"{count_signals(results, 'Structure_CHOCH')}"
    )

    print(
        f"Liquidity zones: "
        f"{count_signals(results, 'Liquidity_Liquidity')}"
    )

    print(
        f"Order blocks:    "
        f"{count_signals(results, 'OB_OB')}"
    )

    print(
        f"Swing trend:     "
        f"{latest.get('Trend_State', 'Unavailable')}"
    )

    print(
        f"Dealing range:   "
        f"{latest.get('Trend_Zone', 'Unavailable')}"
    )


# =========================================================
# MAIN PROGRAM
# =========================================================

def main() -> None:
    print("Connecting to MetaTrader 5...")

    if not mt5.initialize():
        raise RuntimeError(
            "Could not connect to MetaTrader 5.\n"
            f"MetaTrader error: {mt5.last_error()}"
        )

    live_server = None

    try:
        print(
            f"Downloading {NUMBER_OF_CANDLES} completed "
            f"{SYMBOL} {TIMEFRAME_NAME} candles..."
        )

        candles = get_mt5_candles(
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            candle_count=NUMBER_OF_CANDLES,
        )

        print(
            f"Downloaded {len(candles)} candles."
        )

        print(
            "Calculating indicators and creating the dashboard..."
        )

        results, csv_path, chart_path = (
            analyze_and_write_outputs(
                candles
            )
        )

        print_summary(results)

        print("\n========================================")
        print("FILES CREATED")
        print("========================================")

        print(f"CSV file:   {csv_path}")
        print(f"Chart file: {chart_path}")
        print(
            "MPL chart:  "
            f"{Path(MPLFINANCE_OUTPUT_FILE).resolve()}"
        )
        print(
            "Backtest:   "
            f"{Path(BACKTEST_OUTPUT_FILE).resolve()}"
        )
        print(
            "Trades:     "
            f"{Path(BACKTEST_TRADES_FILE).resolve()}"
        )

        last_candle_time = pd.Timestamp(
            results["time"].iloc[-1]
        )
        fallback_price = float(
            results["close"].iloc[-1]
        )
        live_price = get_mt5_live_price(
            fallback_price
        )

        update_live_state(
            last_candle_time=last_candle_time,
            last_price=live_price,
            increment_version=True,
        )

        if not LIVE_MODE:
            print("\nOpening static chart in your browser...")
            webbrowser.open_new_tab(
                chart_path.as_uri()
            )
            return

        live_server, dashboard_url = (
            start_live_dashboard_server()
        )

        print("\n========================================")
        print("LIVE DASHBOARD")
        print("========================================")
        print(f"URL: {dashboard_url}")
        print(
            f"Checking MT5 every {LIVE_REFRESH_SECONDS} seconds."
        )
        print(
            "The chart recalculates after each completed "
            f"{TIMEFRAME_NAME} candle. Press Ctrl+C to stop."
        )

        webbrowser.open_new_tab(
            dashboard_url
        )

        last_error_message = None

        while True:
            time.sleep(
                LIVE_REFRESH_SECONDS
            )

            try:
                recent_candles = get_mt5_candles(
                    symbol=SYMBOL,
                    timeframe=TIMEFRAME,
                    candle_count=2,
                )

                newest_candle_time = pd.Timestamp(
                    recent_candles["time"].iloc[-1]
                )

                live_price = get_mt5_live_price(
                    fallback_price
                )

                if newest_candle_time > last_candle_time:
                    print(
                        "\nNew completed candle detected: "
                        f"{newest_candle_time}"
                    )

                    candles = get_mt5_candles(
                        symbol=SYMBOL,
                        timeframe=TIMEFRAME,
                        candle_count=NUMBER_OF_CANDLES,
                    )

                    results, csv_path, chart_path = (
                        analyze_and_write_outputs(
                            candles
                        )
                    )

                    last_candle_time = pd.Timestamp(
                        results["time"].iloc[-1]
                    )
                    fallback_price = float(
                        results["close"].iloc[-1]
                    )

                    update_live_state(
                        last_candle_time=last_candle_time,
                        last_price=live_price,
                        increment_version=True,
                    )

                    print(
                        "Dashboard refreshed and all SMC "
                        "indicators recalculated."
                    )
                else:
                    update_live_state(
                        last_candle_time=last_candle_time,
                        last_price=live_price,
                    )

                if last_error_message is not None:
                    print("MT5 live connection recovered.")
                    last_error_message = None

            except Exception as live_error:
                error_message = str(live_error)

                update_live_state(
                    last_candle_time=last_candle_time,
                    last_price=live_price,
                    error=error_message,
                )

                if error_message != last_error_message:
                    print(
                        "\nLive update warning: "
                        f"{error_message}"
                    )
                    last_error_message = error_message

    except KeyboardInterrupt:
        print("\nLive dashboard stopped by user.")

    except Exception as error:
        print(f"\nError: {error}")

    finally:
        if live_server is not None:
            live_server.shutdown()
            live_server.server_close()

        mt5.shutdown()
        print(
            "\nMetaTrader 5 connection closed."
        )


if __name__ == "__main__":
    main()
