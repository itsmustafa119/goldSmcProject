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

# Historical candles downloaded from MetaTrader 5.
NUMBER_OF_CANDLES = 5000

# Candles initially displayed on the chart.
CHART_CANDLES = 1200

SWING_LENGTH = 20
LIQUIDITY_RANGE = 0.01

# Limit visible active zones to keep the chart clean.
MAX_FVG_ZONES = 8
MAX_OB_ZONES = 6
MAX_LIQUIDITY_LEVELS = 6

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
            .add_prefix("Daily_"),
        ],
        axis=1,
    )

    return results


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


def select_nearest_active_zones(
    data: pd.DataFrame,
    signal_column: str,
    top_column: str,
    bottom_column: str,
    mitigation_column: str,
    current_price: float,
    maximum_zones: int,
) -> pd.DataFrame:
    """Select active zones closest to current price."""

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

    if mitigation_column in zones.columns:
        zones = zones[
            zones[mitigation_column].apply(
                indicator_is_active
            )
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

    return (
        zones
        .nsmallest(maximum_zones, "_distance")
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
                "width": 1.4,
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
                "<extra></extra>"
            ),
        )
    )


def add_structure_markers(
    fig: go.Figure,
    data: pd.DataFrame,
    first_time,
    signal_column: str,
    definitions,
) -> None:
    """Add BOS or CHoCH markers without permanent text."""

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

        signal_times = []
        signal_levels = []
        hover_text = []

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

            signal_times.append(signal_time)
            signal_levels.append(level)

            hover_text.append(
                f"<b>{label}</b><br>"
                f"Level: {level:.2f}<br>"
                f"Time: {signal_time}"
            )

        fig.add_trace(
            go.Scatter(
                x=signal_times,
                y=signal_levels,
                mode="markers",
                marker={
                    "symbol": symbol,
                    "size": 13,
                    "color": color,
                    "line": {
                        "color": "white",
                        "width": 1,
                    },
                },
                name=label,
                hovertext=hover_text,
                hoverinfo="text",
            )
        )


# =========================================================
# CREATE INTERACTIVE CHART
# =========================================================

def create_interactive_chart(
    results: pd.DataFrame,
    number_of_candles: int = 1200,
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

    shown_groups = set()

    # -----------------------------------------------------
    # ACTIVE FAIR VALUE GAPS
    # -----------------------------------------------------

    fvg_zones = select_nearest_active_zones(
        data=data,
        signal_column="FVG_FVG",
        top_column="FVG_Top",
        bottom_column="FVG_Bottom",
        mitigation_column="FVG_MitigatedIndex",
        current_price=current_price,
        maximum_zones=MAX_FVG_ZONES,
    )

    for _, row in fvg_zones.iterrows():

        bullish = row["FVG_FVG"] == 1

        if bullish:
            label = "Bullish FVG"
            group = "bullish_fvg"
            fill_color = "rgba(0,205,110,0.18)"
            border_color = "rgba(0,240,130,0.95)"
        else:
            label = "Bearish FVG"
            group = "bearish_fvg"
            fill_color = "rgba(235,55,75,0.18)"
            border_color = "rgba(255,75,95,0.95)"

        add_zone_trace(
            fig=fig,
            start_time=max(
                row["time"],
                first_time,
            ),
            end_time=last_time,
            bottom=row["FVG_Bottom"],
            top=row["FVG_Top"],
            label=label,
            legend_group=group,
            fill_color=fill_color,
            border_color=border_color,
            show_legend=group not in shown_groups,
        )

        shown_groups.add(group)

    # -----------------------------------------------------
    # ACTIVE ORDER BLOCKS
    # -----------------------------------------------------

    order_block_zones = select_nearest_active_zones(
        data=data,
        signal_column="OB_OB",
        top_column="OB_Top",
        bottom_column="OB_Bottom",
        mitigation_column="OB_MitigatedIndex",
        current_price=current_price,
        maximum_zones=MAX_OB_ZONES,
    )

    for _, row in order_block_zones.iterrows():

        bullish = row["OB_OB"] == 1

        if bullish:
            label = "Bullish Order Block"
            group = "bullish_ob"
            fill_color = "rgba(35,125,255,0.18)"
            border_color = "rgba(65,160,255,0.95)"
        else:
            label = "Bearish Order Block"
            group = "bearish_ob"
            fill_color = "rgba(255,140,25,0.18)"
            border_color = "rgba(255,175,45,0.95)"

        add_zone_trace(
            fig=fig,
            start_time=max(
                row["time"],
                first_time,
            ),
            end_time=last_time,
            bottom=row["OB_Bottom"],
            top=row["OB_Top"],
            label=label,
            legend_group=group,
            fill_color=fill_color,
            border_color=border_color,
            show_legend=group not in shown_groups,
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
            group = "buy_side_liquidity"
            line_color = "#d500f9"
        else:
            label = "Sell-side Liquidity"
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
                mode="lines",
                line={
                    "color": line_color,
                    "width": 2,
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
            line_width=2,
            line_dash="dash",
            annotation_text=(
                f"PDH {previous_high:.2f}"
            ),
            annotation_position="top right",
        )

    if previous_low is not None:

        previous_low = float(
            previous_low
        )

        fig.add_hline(
            y=previous_low,
            line_color="#ff00e6",
            line_width=2,
            line_dash="dash",
            annotation_text=(
                f"PDL {previous_low:.2f}"
            ),
            annotation_position="bottom right",
        )

    # -----------------------------------------------------
    # CURRENT PRICE
    # -----------------------------------------------------

    fig.add_hline(
        y=current_price,
        line_color="white",
        line_width=1.4,
        line_dash="dot",
        annotation_text=(
            f"Price {current_price:.2f}"
        ),
        annotation_position="bottom right",
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
                "Hover over zones for details. "
                "Click legend items to hide or show groups."
                "</sup>"
            ),
            "x": 0.5,
            "xanchor": "center",
        },
        template="plotly_dark",
        height=1050,
        autosize=True,
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

    fig.update_xaxes(
        range=[
            first_time,
            last_time
            + pd.Timedelta(
                minutes=TIMEFRAME_MINUTES * 3
            ),
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
        rangeselector={
            "buttons": [
                {
                    "count": 1,
                    "label": "1D",
                    "step": "day",
                    "stepmode": "backward",
                },
                {
                    "count": 3,
                    "label": "3D",
                    "step": "day",
                    "stepmode": "backward",
                },
                {
                    "count": 7,
                    "label": "1W",
                    "step": "day",
                    "stepmode": "backward",
                },
                {
                    "step": "all",
                    "label": "All",
                },
            ],
            "x": 0,
            "y": 1.08,
        },
        gridcolor="#29313a",
        showspikes=True,
        spikecolor="#dddddd",
        spikemode="across",
    )

    fig.update_yaxes(
        gridcolor="#29313a",
        tickformat=".2f",
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
        "modeBarButtonsToAdd": [
            "drawline",
            "drawrect",
            "eraseshape",
        ],
        "toImageButtonOptions": {
            "format": "png",
            "filename": "xauusd_m15_smc_chart",
            "width": 2200,
            "height": 1200,
            "scale": 2,
        },
    }

    fig.write_html(
        str(output_path),
        include_plotlyjs=True,
        full_html=True,
        auto_open=False,
        config=chart_config,
    )

    return output_path


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

        print(
            "Creating the clean interactive chart..."
        )

        chart_path = create_interactive_chart(
            results=results,
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

        print(
            "\nMetaTrader 5 connection closed."
        )


if __name__ == "__main__":
    main()