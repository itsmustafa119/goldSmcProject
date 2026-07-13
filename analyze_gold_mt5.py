from pathlib import Path
import webbrowser

import MetaTrader5 as mt5
import pandas as pd
import plotly.graph_objects as go
from smartmoneyconcepts import smc


# =========================================================
# SETTINGS
# =========================================================

SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M15
TIMEFRAME_NAME = "M15"
TIMEFRAME_MINUTES = 15

# More history downloaded from MetaTrader 5
NUMBER_OF_CANDLES = 5000

# Number of candles initially displayed on the chart
CHART_CANDLES = 1000

SWING_LENGTH = 20
LIQUIDITY_RANGE = 0.01

# Maximum number of zones added to the chart
MAX_FVG_ZONES = 80
MAX_OB_ZONES = 50
MAX_LIQUIDITY_LEVELS = 30

# Keep True to show both active and already mitigated zones
SHOW_MITIGATED_ZONES = True

CSV_OUTPUT_FILE = "xauusd_m15_smc_results.csv"
HTML_OUTPUT_FILE = "xauusd_m15_smc_chart.html"


# =========================================================
# FIND GOLD SYMBOLS
# =========================================================

def find_gold_symbols() -> list[str]:
    """Find possible gold symbols in MetaTrader 5."""

    symbols = mt5.symbols_get()

    if symbols is None:
        return []

    return [
        symbol.name
        for symbol in symbols
        if "XAU" in symbol.name.upper()
        or "GOLD" in symbol.name.upper()
    ]


# =========================================================
# DOWNLOAD CANDLES
# =========================================================

def get_mt5_candles(
    symbol: str,
    timeframe: int,
    candle_count: int,
) -> pd.DataFrame:
    """Download completed candles from MetaTrader 5."""

    symbol_info = mt5.symbol_info(symbol)

    if symbol_info is None:
        possible_symbols = find_gold_symbols()

        raise ValueError(
            f"Symbol '{symbol}' was not found.\n"
            f"Possible gold symbols: {possible_symbols}"
        )

    if not symbol_info.visible:
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(
                f"Could not enable {symbol} in Market Watch."
            )

    # Candle 0 is still forming.
    # Start from candle 1 to use only completed candles.
    rates = mt5.copy_rates_from_pos(
        symbol,
        timeframe,
        1,
        candle_count,
    )

    if rates is None or len(rates) == 0:
        raise RuntimeError(
            f"No candle data was received for {symbol}.\n"
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

    data = data[required_columns].copy()

    data = data.sort_values("time")
    data = data.reset_index(drop=True)

    return data


# =========================================================
# CALCULATE SMC INDICATORS
# =========================================================

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

    # previous_high_low requires datetime as the index.
    indexed_data = data.set_index("time")[
        [
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    ].copy()

    previous_levels = smc.previous_high_low(
        indexed_data,
        time_frame="1D",
    )

    previous_levels = previous_levels.reset_index(
        drop=True
    )

    results = pd.concat(
        [
            data.reset_index(drop=True),

            swings
            .reset_index(drop=True)
            .add_prefix("Swing_"),

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
            .reset_index(drop=True)
            .add_prefix("Daily_"),
        ],
        axis=1,
    )

    return results


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def is_valid_indicator_index(value) -> bool:
    """Check whether an indicator index points to a real candle."""

    if pd.isna(value):
        return False

    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def indicator_index_to_time(
    data: pd.DataFrame,
    index_value,
    default_time,
):
    """Convert an indicator candle index into candle time."""

    if not is_valid_indicator_index(index_value):
        return default_time

    candle_index = int(index_value)

    if 0 <= candle_index < len(data):
        return data.iloc[candle_index]["time"]

    return default_time


def get_zone_end_time(
    data: pd.DataFrame,
    index_value,
    latest_time,
):
    """
    Return mitigation/sweep time.

    A value of zero or NaN means that the zone remains active.
    """

    if not is_valid_indicator_index(index_value):
        return latest_time

    candle_index = int(index_value)

    if 0 <= candle_index < len(data):
        return data.iloc[candle_index]["time"]

    return latest_time


def get_latest_nonempty_value(
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


def add_zone_to_chart(
    fig: go.Figure,
    start_time,
    end_time,
    bottom: float,
    top: float,
    label: str,
    fill_color: str,
    border_color: str,
) -> None:
    """Add a labelled rectangular zone."""

    if pd.isna(bottom) or pd.isna(top):
        return

    bottom = float(bottom)
    top = float(top)

    if end_time <= start_time:
        return

    if top < bottom:
        top, bottom = bottom, top

    fig.add_shape(
        type="rect",
        x0=start_time,
        x1=end_time,
        y0=bottom,
        y1=top,
        fillcolor=fill_color,
        line={
            "color": border_color,
            "width": 1.5,
        },
        layer="below",
    )

    middle_time = (
        start_time
        + (end_time - start_time) / 2
    )

    middle_price = (top + bottom) / 2

    fig.add_annotation(
        x=middle_time,
        y=middle_price,
        text=f"<b>{label}</b>",
        showarrow=False,
        font={
            "size": 11,
            "color": "white",
        },
        bgcolor="rgba(15, 15, 15, 0.75)",
        bordercolor=border_color,
        borderwidth=1,
        borderpad=3,
        opacity=0.95,
    )


def add_legend_guides(
    fig: go.Figure,
) -> None:
    """Add clear legend entries for chart zones."""

    legend_items = [
        (
            "Bullish FVG",
            "rgba(0, 210, 110, 0.65)",
            "rgba(0, 230, 120, 1)",
        ),
        (
            "Bearish FVG",
            "rgba(230, 50, 70, 0.65)",
            "rgba(255, 70, 90, 1)",
        ),
        (
            "Bullish Order Block",
            "rgba(30, 130, 255, 0.65)",
            "rgba(60, 160, 255, 1)",
        ),
        (
            "Bearish Order Block",
            "rgba(255, 140, 20, 0.65)",
            "rgba(255, 170, 40, 1)",
        ),
    ]

    for name, fill_color, border_color in legend_items:
        fig.add_trace(
            go.Scatter(
                x=[None],
                y=[None],
                mode="markers",
                marker={
                    "symbol": "square",
                    "size": 15,
                    "color": fill_color,
                    "line": {
                        "color": border_color,
                        "width": 1.5,
                    },
                },
                name=name,
                hoverinfo="skip",
            )
        )


# =========================================================
# CREATE INTERACTIVE CHART
# =========================================================

def create_interactive_chart(
    results: pd.DataFrame,
    number_of_candles: int,
) -> Path:
    """Create an interactive and user-friendly chart."""

    data = results.copy()

    data["time"] = pd.to_datetime(
        data["time"]
    )

    chart_data = data.tail(
        number_of_candles
    ).copy()

    if chart_data.empty:
        raise ValueError(
            "No candle data is available for the chart."
        )

    first_time = chart_data["time"].iloc[0]
    last_time = chart_data["time"].iloc[-1]

    fig = go.Figure()

    # -----------------------------------------------------
    # Candlesticks
    # -----------------------------------------------------

    fig.add_trace(
        go.Candlestick(
            x=chart_data["time"],
            open=chart_data["open"],
            high=chart_data["high"],
            low=chart_data["low"],
            close=chart_data["close"],
            name=f"{SYMBOL} {TIMEFRAME_NAME}",
            increasing_line_color="#16c7a3",
            increasing_fillcolor="#16c7a3",
            decreasing_line_color="#ff4d5a",
            decreasing_fillcolor="#ff4d5a",
            whiskerwidth=0.4,
            hovertext=[
                (
                    f"Time: {row.time}<br>"
                    f"Open: {row.open:.2f}<br>"
                    f"High: {row.high:.2f}<br>"
                    f"Low: {row.low:.2f}<br>"
                    f"Close: {row.close:.2f}"
                )
                for row in chart_data.itertuples()
            ],
            hoverinfo="text",
        )
    )

    add_legend_guides(fig)

    # -----------------------------------------------------
    # BOS
    # -----------------------------------------------------

    required_bos_columns = {
        "Structure_BOS",
        "Structure_Level",
        "Structure_BrokenIndex",
    }

    if required_bos_columns.issubset(data.columns):
        bos_rows = data[
            data["Structure_BOS"].fillna(0).ne(0)
            & data["Structure_Level"].notna()
        ].copy()

        bullish_times = []
        bullish_levels = []
        bullish_hover = []

        bearish_times = []
        bearish_levels = []
        bearish_hover = []

        for _, row in bos_rows.iterrows():
            signal_time = indicator_index_to_time(
                data,
                row["Structure_BrokenIndex"],
                row["time"],
            )

            if signal_time < first_time:
                continue

            level = float(row["Structure_Level"])

            if row["Structure_BOS"] == 1:
                bullish_times.append(signal_time)
                bullish_levels.append(level)
                bullish_hover.append(
                    f"Bullish BOS<br>"
                    f"Level: {level:.2f}<br>"
                    f"Time: {signal_time}"
                )

            elif row["Structure_BOS"] == -1:
                bearish_times.append(signal_time)
                bearish_levels.append(level)
                bearish_hover.append(
                    f"Bearish BOS<br>"
                    f"Level: {level:.2f}<br>"
                    f"Time: {signal_time}"
                )

        fig.add_trace(
            go.Scatter(
                x=bullish_times,
                y=bullish_levels,
                mode="markers+text",
                marker={
                    "symbol": "triangle-up",
                    "size": 17,
                    "color": "#00e676",
                    "line": {
                        "color": "white",
                        "width": 1,
                    },
                },
                text=["Bullish BOS"] * len(bullish_times),
                textposition="bottom center",
                textfont={
                    "size": 11,
                    "color": "#00e676",
                },
                hovertext=bullish_hover,
                hoverinfo="text",
                name="Bullish BOS",
            )
        )

        fig.add_trace(
            go.Scatter(
                x=bearish_times,
                y=bearish_levels,
                mode="markers+text",
                marker={
                    "symbol": "triangle-down",
                    "size": 17,
                    "color": "#ff1744",
                    "line": {
                        "color": "white",
                        "width": 1,
                    },
                },
                text=["Bearish BOS"] * len(bearish_times),
                textposition="top center",
                textfont={
                    "size": 11,
                    "color": "#ff5252",
                },
                hovertext=bearish_hover,
                hoverinfo="text",
                name="Bearish BOS",
            )
        )

    # -----------------------------------------------------
    # CHoCH
    # -----------------------------------------------------

    required_choch_columns = {
        "Structure_CHOCH",
        "Structure_Level",
        "Structure_BrokenIndex",
    }

    if required_choch_columns.issubset(data.columns):
        choch_rows = data[
            data["Structure_CHOCH"].fillna(0).ne(0)
            & data["Structure_Level"].notna()
        ].copy()

        bullish_times = []
        bullish_levels = []
        bullish_hover = []

        bearish_times = []
        bearish_levels = []
        bearish_hover = []

        for _, row in choch_rows.iterrows():
            signal_time = indicator_index_to_time(
                data,
                row["Structure_BrokenIndex"],
                row["time"],
            )

            if signal_time < first_time:
                continue

            level = float(row["Structure_Level"])

            if row["Structure_CHOCH"] == 1:
                bullish_times.append(signal_time)
                bullish_levels.append(level)
                bullish_hover.append(
                    f"Bullish CHoCH<br>"
                    f"Level: {level:.2f}<br>"
                    f"Time: {signal_time}"
                )

            elif row["Structure_CHOCH"] == -1:
                bearish_times.append(signal_time)
                bearish_levels.append(level)
                bearish_hover.append(
                    f"Bearish CHoCH<br>"
                    f"Level: {level:.2f}<br>"
                    f"Time: {signal_time}"
                )

        fig.add_trace(
            go.Scatter(
                x=bullish_times,
                y=bullish_levels,
                mode="markers+text",
                marker={
                    "symbol": "diamond",
                    "size": 16,
                    "color": "#00e5ff",
                    "line": {
                        "color": "white",
                        "width": 1,
                    },
                },
                text=["Bullish CHoCH"] * len(bullish_times),
                textposition="bottom center",
                textfont={
                    "size": 11,
                    "color": "#00e5ff",
                },
                hovertext=bullish_hover,
                hoverinfo="text",
                name="Bullish CHoCH",
            )
        )

        fig.add_trace(
            go.Scatter(
                x=bearish_times,
                y=bearish_levels,
                mode="markers+text",
                marker={
                    "symbol": "diamond",
                    "size": 16,
                    "color": "#ffb300",
                    "line": {
                        "color": "white",
                        "width": 1,
                    },
                },
                text=["Bearish CHoCH"] * len(bearish_times),
                textposition="top center",
                textfont={
                    "size": 11,
                    "color": "#ffca28",
                },
                hovertext=bearish_hover,
                hoverinfo="text",
                name="Bearish CHoCH",
            )
        )

    # -----------------------------------------------------
    # FAIR VALUE GAPS
    # -----------------------------------------------------

    required_fvg_columns = {
        "FVG_FVG",
        "FVG_Top",
        "FVG_Bottom",
    }

    if required_fvg_columns.issubset(data.columns):
        fvg_rows = data[
            data["FVG_FVG"].notna()
            & data["FVG_Top"].notna()
            & data["FVG_Bottom"].notna()
        ].tail(MAX_FVG_ZONES)

        for _, row in fvg_rows.iterrows():
            mitigation_index = row.get(
                "FVG_MitigatedIndex"
            )

            is_active = not is_valid_indicator_index(
                mitigation_index
            )

            if not SHOW_MITIGATED_ZONES and not is_active:
                continue

            zone_start = row["time"]

            zone_end = get_zone_end_time(
                data,
                mitigation_index,
                last_time,
            )

            if zone_end < first_time:
                continue

            if zone_start > last_time:
                continue

            if zone_start < first_time:
                zone_start = first_time

            if zone_end > last_time:
                zone_end = last_time

            bullish = row["FVG_FVG"] == 1

            if bullish:
                label = (
                    "Bullish FVG — Active"
                    if is_active
                    else "Bullish FVG"
                )

                fill_color = (
                    "rgba(0, 210, 110, 0.28)"
                    if is_active
                    else "rgba(0, 210, 110, 0.13)"
                )

                border_color = (
                    "rgba(0, 240, 125, 0.95)"
                )

            else:
                label = (
                    "Bearish FVG — Active"
                    if is_active
                    else "Bearish FVG"
                )

                fill_color = (
                    "rgba(235, 45, 65, 0.28)"
                    if is_active
                    else "rgba(235, 45, 65, 0.13)"
                )

                border_color = (
                    "rgba(255, 70, 90, 0.95)"
                )

            add_zone_to_chart(
                fig=fig,
                start_time=zone_start,
                end_time=zone_end,
                bottom=row["FVG_Bottom"],
                top=row["FVG_Top"],
                label=label,
                fill_color=fill_color,
                border_color=border_color,
            )

    # -----------------------------------------------------
    # ORDER BLOCKS
    # -----------------------------------------------------

    required_ob_columns = {
        "OB_OB",
        "OB_Top",
        "OB_Bottom",
    }

    if required_ob_columns.issubset(data.columns):
        order_block_rows = data[
            data["OB_OB"].notna()
            & data["OB_Top"].notna()
            & data["OB_Bottom"].notna()
        ].tail(MAX_OB_ZONES)

        for _, row in order_block_rows.iterrows():
            mitigation_index = row.get(
                "OB_MitigatedIndex"
            )

            is_active = not is_valid_indicator_index(
                mitigation_index
            )

            if not SHOW_MITIGATED_ZONES and not is_active:
                continue

            zone_start = row["time"]

            zone_end = get_zone_end_time(
                data,
                mitigation_index,
                last_time,
            )

            if zone_end < first_time:
                continue

            if zone_start > last_time:
                continue

            if zone_start < first_time:
                zone_start = first_time

            if zone_end > last_time:
                zone_end = last_time

            bullish = row["OB_OB"] == 1

            if bullish:
                label = (
                    "Bullish OB — Active"
                    if is_active
                    else "Bullish OB"
                )

                fill_color = (
                    "rgba(30, 125, 255, 0.27)"
                    if is_active
                    else "rgba(30, 125, 255, 0.12)"
                )

                border_color = (
                    "rgba(65, 155, 255, 0.95)"
                )

            else:
                label = (
                    "Bearish OB — Active"
                    if is_active
                    else "Bearish OB"
                )

                fill_color = (
                    "rgba(255, 135, 20, 0.27)"
                    if is_active
                    else "rgba(255, 135, 20, 0.12)"
                )

                border_color = (
                    "rgba(255, 170, 45, 0.95)"
                )

            add_zone_to_chart(
                fig=fig,
                start_time=zone_start,
                end_time=zone_end,
                bottom=row["OB_Bottom"],
                top=row["OB_Top"],
                label=label,
                fill_color=fill_color,
                border_color=border_color,
            )

    # -----------------------------------------------------
    # LIQUIDITY LEVELS
    # -----------------------------------------------------

    required_liquidity_columns = {
        "Liquidity_Liquidity",
        "Liquidity_Level",
    }

    if required_liquidity_columns.issubset(data.columns):
        liquidity_rows = data[
            data["Liquidity_Liquidity"].notna()
            & data["Liquidity_Level"].notna()
        ].tail(MAX_LIQUIDITY_LEVELS)

        for _, row in liquidity_rows.iterrows():
            level_start = row["time"]

            swept_index = row.get(
                "Liquidity_Swept"
            )

            is_active = not is_valid_indicator_index(
                swept_index
            )

            level_end = get_zone_end_time(
                data,
                swept_index,
                last_time,
            )

            if level_end < first_time:
                continue

            if level_start > last_time:
                continue

            if level_start < first_time:
                level_start = first_time

            if level_end > last_time:
                level_end = last_time

            level = float(
                row["Liquidity_Level"]
            )

            if row["Liquidity_Liquidity"] == 1:
                line_color = "#d500f9"
                label = (
                    "High Liquidity — Active"
                    if is_active
                    else "High Liquidity"
                )
            else:
                line_color = "#ffea00"
                label = (
                    "Low Liquidity — Active"
                    if is_active
                    else "Low Liquidity"
                )

            fig.add_shape(
                type="line",
                x0=level_start,
                x1=level_end,
                y0=level,
                y1=level,
                line={
                    "color": line_color,
                    "width": 2,
                    "dash": "dot",
                },
            )

            fig.add_annotation(
                x=level_end,
                y=level,
                text=f"<b>{label}</b>",
                showarrow=False,
                xanchor="right",
                yshift=10,
                font={
                    "color": line_color,
                    "size": 11,
                },
                bgcolor="rgba(15, 15, 15, 0.75)",
                bordercolor=line_color,
                borderwidth=1,
                borderpad=3,
            )

    # -----------------------------------------------------
    # PREVIOUS DAILY HIGH
    # -----------------------------------------------------

    previous_high = get_latest_nonempty_value(
        chart_data,
        "Daily_PreviousHigh",
    )

    if previous_high is not None:
        previous_high = float(previous_high)

        fig.add_hline(
            y=previous_high,
            line_dash="dash",
            line_width=2,
            line_color="#00e5ff",
            annotation_text=(
                f"Previous Daily High: "
                f"{previous_high:.2f}"
            ),
            annotation_position="top right",
            annotation_font={
                "size": 12,
                "color": "#00e5ff",
            },
        )

    # -----------------------------------------------------
    # PREVIOUS DAILY LOW
    # -----------------------------------------------------

    previous_low = get_latest_nonempty_value(
        chart_data,
        "Daily_PreviousLow",
    )

    if previous_low is not None:
        previous_low = float(previous_low)

        fig.add_hline(
            y=previous_low,
            line_dash="dash",
            line_width=2,
            line_color="#ff00e6",
            annotation_text=(
                f"Previous Daily Low: "
                f"{previous_low:.2f}"
            ),
            annotation_position="bottom right",
            annotation_font={
                "size": 12,
                "color": "#ff55eb",
            },
        )

    # -----------------------------------------------------
    # CURRENT PRICE
    # -----------------------------------------------------

    latest_close = float(
        chart_data["close"].iloc[-1]
    )

    fig.add_hline(
        y=latest_close,
        line_dash="dot",
        line_width=1.5,
        line_color="white",
        annotation_text=(
            f"Current Price: {latest_close:.2f}"
        ),
        annotation_position="bottom right",
        annotation_font={
            "size": 11,
            "color": "white",
        },
    )

    # -----------------------------------------------------
    # LAYOUT
    # -----------------------------------------------------

    right_padding = pd.Timedelta(
        minutes=TIMEFRAME_MINUTES * 2
    )

    range_breaks = [
        {
            "bounds": ["sat", "mon"],
        }
    ]

    fig.update_layout(
        title={
            "text": (
                f"<b>{SYMBOL} {TIMEFRAME_NAME}</b>"
                "<br>"
                "<sup>Smart Money Concepts Analysis</sup>"
            ),
            "x": 0.5,
            "xanchor": "center",
            "font": {
                "size": 24,
            },
        },
        template="plotly_dark",
        autosize=True,
        height=1100,
        hovermode="closest",
        dragmode="pan",
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
            "y": 1.01,
            "xanchor": "center",
            "x": 0.5,
            "bgcolor": "rgba(15, 15, 15, 0.75)",
            "bordercolor": "#444",
            "borderwidth": 1,
            "font": {
                "size": 11,
            },
        },
        margin={
            "l": 80,
            "r": 190,
            "t": 145,
            "b": 100,
        },
        hoverlabel={
            "bgcolor": "#1d2127",
            "font_size": 12,
            "font_family": "Arial",
        },
        modebar={
            "orientation": "v",
            "bgcolor": "rgba(30, 30, 30, 0.8)",
            "color": "white",
            "activecolor": "#00e5ff",
        },
    )

    fig.update_xaxes(
        type="date",
        range=[
            first_time,
            last_time + right_padding,
        ],
        rangebreaks=range_breaks,
        rangeslider={
            "visible": True,
            "thickness": 0.08,
            "bgcolor": "#181b20",
            "bordercolor": "#444",
            "borderwidth": 1,
        },
        rangeselector={
            "buttons": [
                {
                    "count": 1,
                    "label": "1 Day",
                    "step": "day",
                    "stepmode": "backward",
                },
                {
                    "count": 3,
                    "label": "3 Days",
                    "step": "day",
                    "stepmode": "backward",
                },
                {
                    "count": 7,
                    "label": "1 Week",
                    "step": "day",
                    "stepmode": "backward",
                },
                {
                    "count": 14,
                    "label": "2 Weeks",
                    "step": "day",
                    "stepmode": "backward",
                },
                {
                    "step": "all",
                    "label": "All",
                },
            ],
            "bgcolor": "#22262c",
            "activecolor": "#00a8cc",
            "bordercolor": "#555",
            "borderwidth": 1,
            "font": {
                "color": "white",
                "size": 11,
            },
            "x": 0,
            "y": 1.08,
        },
        showgrid=True,
        gridcolor="#29313a",
        gridwidth=1,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="#dddddd",
        spikethickness=1,
        tickfont={
            "size": 12,
        },
    )

    fig.update_yaxes(
        showgrid=True,
        gridcolor="#29313a",
        gridwidth=1,
        fixedrange=False,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="#dddddd",
        spikethickness=1,
        tickformat=".2f",
        tickfont={
            "size": 12,
        },
    )

    fig.add_annotation(
        xref="paper",
        yref="paper",
        x=0,
        y=-0.16,
        text=(
            "Mouse wheel: zoom | Drag: move chart | "
            "Double-click: reset | Camera icon: save PNG"
        ),
        showarrow=False,
        font={
            "size": 11,
            "color": "#aeb7c2",
        },
        xanchor="left",
    )

    output_path = Path(
        HTML_OUTPUT_FILE
    ).resolve()

    chart_config = {
        "responsive": True,
        "scrollZoom": True,
        "displaylogo": False,
        "showTips": True,
        "modeBarButtonsToAdd": [
            "drawline",
            "drawopenpath",
            "drawrect",
            "eraseshape",
        ],
        "toImageButtonOptions": {
            "format": "png",
            "filename": "xauusd_m15_smc_chart",
            "height": 1200,
            "width": 2200,
            "scale": 2,
        },
    }

    fig.write_html(
        str(output_path),
        include_plotlyjs=True,
        auto_open=False,
        config=chart_config,
        full_html=True,
    )

    return output_path


# =========================================================
# TERMINAL SUMMARY
# =========================================================

def count_signals(
    results: pd.DataFrame,
    column: str,
) -> int:
    """Count real non-zero signals."""

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

    previous_high = get_latest_nonempty_value(
        results,
        "Daily_PreviousHigh",
    )

    previous_low = get_latest_nonempty_value(
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

        print("Calculating SMC indicators...")

        results = calculate_smc_indicators(
            candles
        )

        csv_path = Path(
            CSV_OUTPUT_FILE
        ).resolve()

        results.to_csv(
            csv_path,
            index=False,
        )

        print("Creating the interactive chart...")

        chart_path = create_interactive_chart(
            results,
            number_of_candles=CHART_CANDLES,
        )

        print_summary(results)

        print("\n========================================")
        print("FILES CREATED")
        print("========================================")

        print(f"CSV file:   {csv_path}")
        print(f"Chart file: {chart_path}")

        print("\nOpening chart in your browser...")

        webbrowser.open_new_tab(
            chart_path.as_uri()
        )

    except Exception as error:
        print(f"\nError: {error}")

    finally:
        mt5.shutdown()
        print("\nMetaTrader 5 connection closed.")


if __name__ == "__main__":
    main()