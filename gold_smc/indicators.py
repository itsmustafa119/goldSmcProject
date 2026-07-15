import pandas as pd
from smartmoneyconcepts import smc

from .config import (
    LIQUIDITY_RANGE,
    SESSION_COLORS,
    SESSION_TIME_ZONE,
    SWING_LENGTH,
)


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
