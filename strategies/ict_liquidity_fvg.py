"""Causal ICT-style liquidity sweep, structure shift, and FVG pullback."""

from __future__ import annotations

import numpy as np
import pandas as pd

from gold_smc.strategy_templates import ema, prepare_smc_structure_data


STRATEGY_ID = "ict_liquidity_fvg"
STRATEGY_NAME = "ICT liquidity sweep and FVG pullback"
STRATEGY_DESCRIPTION = (
    "Trades the selected market only after a buy-side or sell-side liquidity sweep, "
    "a confirmed market-structure shift in the opposite direction, and a "
    "later pullback into a still-valid fair-value gap. Completed higher-timeframe trend, "
    "premium/discount, and London/New York session filters provide context. "
    "The stop sits beyond the swept extreme and the target uses the nearest "
    "valid opposing liquidity reference or a 2R fallback."
)
STRATEGY_EVIDENCE = (
    "A transparent, testable implementation of the ICT concepts described in "
    "the project: liquidity sweep, displacement/structure shift, FVG "
    "retracement, session timing, and opposing-liquidity targeting."
)
STRATEGY_SOURCE_URL = (
    "https://www.youtube.com/@InnerCircleTrader"
)
BACKTEST_SETTINGS = {
    "cash": 100_000.0,
    "spread": 0.0001,
    "commission": 0.0,
    "margin": 0.05,
    "position_fraction": 0.10,
    "trade_on_close": True,
    "exclusive_orders": True,
    "finalize_trades": True,
}
INDICATOR_COLUMNS = {
    "HTFBias": "Completed higher-timeframe bias",
    "SweepType": "Liquidity sweep",
    "SweepLocation": "Sweep dealing-range location",
    "SweepLevel": "Swept liquidity level",
    "MSSDirection": "Confirmed structure shift",
    "ICTZoneBottom": "FVG bottom",
    "ICTZoneTop": "FVG top",
    "PriceLocation": "Entry dealing-range location",
    "ActiveSession": "Active ICT session",
    "TargetBasis": "Take-profit basis",
    "ATR": "ATR (14)",
}

SWEEP_TO_MSS_BARS = 16
MSS_TO_ENTRY_BARS = 20
FVG_MAX_AGE = 24
ATR_STOP_MULTIPLIER = 1.25
MINIMUM_STOP_FRACTION = 0.001
FALLBACK_RISK_REWARD = 2.0
MINIMUM_LIQUIDITY_TARGET_R = 1.2
MAXIMUM_LIQUIDITY_TARGET_R = 3.0


def _completed_context(
    close: pd.Series,
    rule: str,
    period: int,
) -> tuple[pd.Series, pd.Series]:
    completed_close = close.resample(
        rule,
        label="right",
        closed="left",
    ).last()
    completed_ema = ema(completed_close, period)
    return (
        completed_close.reindex(close.index, method="ffill"),
        completed_ema.reindex(close.index, method="ffill"),
    )


def _context_rules(index: pd.DatetimeIndex) -> tuple[str, str]:
    interval = index.to_series().diff().median()

    if pd.isna(interval) or interval <= pd.Timedelta(minutes=20):
        return "1h", "4h"

    if interval <= pd.Timedelta(minutes=90):
        return "4h", "1D"

    return "1D", "1W"


def _confirmed_structure_events(results: pd.DataFrame) -> pd.Series:
    """Move BOS/CHoCH values to the candle where the break occurred."""

    events = pd.Series(0, index=results.index, dtype="int8")
    required = {
        "Structure_BOS",
        "Structure_CHOCH",
        "Structure_BrokenIndex",
    }

    if not required.issubset(results.columns):
        return events

    rows = results[
        results["Structure_BrokenIndex"].notna()
        & (
            results["Structure_BOS"].fillna(0).ne(0)
            | results["Structure_CHOCH"].fillna(0).ne(0)
        )
    ]

    for _, row in rows.iterrows():
        broken_index = int(row["Structure_BrokenIndex"])

        if broken_index not in events.index:
            continue

        choch = row["Structure_CHOCH"]
        bos = row["Structure_BOS"]
        direction = (
            int(choch)
            if pd.notna(choch) and float(choch) != 0
            else int(bos)
        )
        events.at[broken_index] = direction

    return events


def _liquidity_sweep_events(
    results: pd.DataFrame,
) -> tuple[pd.Series, pd.Series]:
    """Move each liquidity label to its actual future sweep candle."""

    directions = pd.Series(0, index=results.index, dtype="int8")
    levels = pd.Series(np.nan, index=results.index, dtype=float)
    required = {
        "Liquidity_Liquidity",
        "Liquidity_Level",
        "Liquidity_Swept",
    }

    if not required.issubset(results.columns):
        return directions, levels

    rows = results[
        results["Liquidity_Liquidity"].notna()
        & results["Liquidity_Level"].notna()
        & results["Liquidity_Swept"].notna()
        & results["Liquidity_Swept"].gt(0)
    ]

    for _, row in rows.iterrows():
        swept_index = int(row["Liquidity_Swept"])

        if swept_index not in directions.index:
            continue

        directions.at[swept_index] = int(row["Liquidity_Liquidity"])
        levels.at[swept_index] = float(row["Liquidity_Level"])

    return directions, levels


def _nearest_target(
    row: pd.Series,
    *,
    direction: int,
    entry: float,
    risk: float,
) -> tuple[float, str]:
    if direction == 1:
        candidates = {
            "4H previous high": row.get("FourHour_PreviousHigh"),
            "Daily previous high": row.get("Daily_PreviousHigh"),
            "Confirmed range high": row.get("RangeHigh"),
        }
        valid = [
            (float(value), label)
            for label, value in candidates.items()
            if pd.notna(value)
            and float(value) >= entry + risk * MINIMUM_LIQUIDITY_TARGET_R
            and float(value) <= entry + risk * MAXIMUM_LIQUIDITY_TARGET_R
        ]

        if valid:
            return min(valid, key=lambda item: item[0])

        return entry + risk * FALLBACK_RISK_REWARD, "2R fallback"

    candidates = {
        "4H previous low": row.get("FourHour_PreviousLow"),
        "Daily previous low": row.get("Daily_PreviousLow"),
        "Confirmed range low": row.get("RangeLow"),
    }
    valid = [
        (float(value), label)
        for label, value in candidates.items()
        if pd.notna(value)
        and float(value) <= entry - risk * MINIMUM_LIQUIDITY_TARGET_R
        and float(value) >= entry - risk * MAXIMUM_LIQUIDITY_TARGET_R
    ]

    if valid:
        return max(valid, key=lambda item: item[0])

    return entry - risk * FALLBACK_RISK_REWARD, "2R fallback"


def prepare_strategy_data(
    results: pd.DataFrame,
    *,
    swing_confirmation_bars: int,
) -> pd.DataFrame:
    """Create causal ICT entries and explicit initial brackets."""

    source = results.copy().reset_index(drop=True)
    source["time"] = pd.to_datetime(source["time"])
    data = prepare_smc_structure_data(
        source,
        swing_confirmation_bars=swing_confirmation_bars,
    ).copy()
    close = data["Close"].astype(float)
    open_price = data["Open"].astype(float)
    high = data["High"].astype(float)
    low = data["Low"].astype(float)
    atr = data["ATR"].astype(float)

    first_rule, second_rule = _context_rules(data.index)
    h1_close, h1_ema = _completed_context(close, first_rule, 50)
    h4_close, h4_ema = _completed_context(close, second_rule, 50)
    bullish_bias = h1_close.gt(h1_ema) & h4_close.gt(h4_ema)
    bearish_bias = h1_close.lt(h1_ema) & h4_close.lt(h4_ema)
    data["HTFBias"] = np.select(
        [bullish_bias, bearish_bias],
        ["Bullish", "Bearish"],
        default="Neutral / warming up",
    )

    structure_events = _confirmed_structure_events(source)
    sweep_direction, sweep_level = _liquidity_sweep_events(source)
    fvg_direction = (
        pd.to_numeric(source["FVG_FVG"], errors="coerce")
        .shift(1)
        .fillna(0)
        .astype("int8")
    )
    fvg_top = pd.to_numeric(
        source["FVG_Top"],
        errors="coerce",
    ).shift(1)
    fvg_bottom = pd.to_numeric(
        source["FVG_Bottom"],
        errors="coerce",
    ).shift(1)
    london = source["Session_London_Active"].fillna(0).astype(bool)
    new_york = source["Session_NewYork_Active"].fillna(0).astype(bool)
    active_session = london | new_york

    long_signal = pd.Series(False, index=data.index)
    short_signal = pd.Series(False, index=data.index)
    long_sl = close - pd.concat(
        [atr * ATR_STOP_MULTIPLIER, close * MINIMUM_STOP_FRACTION],
        axis=1,
    ).max(axis=1)
    short_sl = close + pd.concat(
        [atr * ATR_STOP_MULTIPLIER, close * MINIMUM_STOP_FRACTION],
        axis=1,
    ).max(axis=1)
    long_tp = close + (close - long_sl) * FALLBACK_RISK_REWARD
    short_tp = close - (short_sl - close) * FALLBACK_RISK_REWARD
    sweep_type = pd.Series("", index=data.index, dtype=object)
    sweep_location = pd.Series("", index=data.index, dtype=object)
    setup_sweep_level = pd.Series(np.nan, index=data.index, dtype=float)
    mss_direction = pd.Series(0, index=data.index, dtype="int8")
    zone_top = pd.Series(np.nan, index=data.index, dtype=float)
    zone_bottom = pd.Series(np.nan, index=data.index, dtype=float)
    target_basis = pd.Series("", index=data.index, dtype=object)
    signal_reason = pd.Series("", index=data.index, dtype=object)

    state = {
        "direction": 0,
        "sweep_index": None,
        "sweep_level": np.nan,
        "sweep_extreme": np.nan,
        "sweep_location_valid": False,
        "mss_index": None,
        "fvg_index": None,
        "fvg_top": np.nan,
        "fvg_bottom": np.nan,
    }

    for position in range(len(data)):
        event = int(sweep_direction.iloc[position])

        if event != 0:
            state = {
                "direction": -event,
                "sweep_index": position,
                "sweep_level": float(sweep_level.iloc[position]),
                "sweep_extreme": (
                    float(low.iloc[position])
                    if event == -1
                    else float(high.iloc[position])
                ),
                "sweep_location_valid": (
                    pd.notna(data["Equilibrium"].iloc[position])
                    and (
                        float(low.iloc[position])
                        < float(data["Equilibrium"].iloc[position])
                        if event == -1
                        else float(high.iloc[position])
                        > float(data["Equilibrium"].iloc[position])
                    )
                ),
                "mss_index": None,
                "fvg_index": None,
                "fvg_top": np.nan,
                "fvg_bottom": np.nan,
            }

        if state["sweep_index"] is None:
            continue

        if position - int(state["sweep_index"]) > (
            SWEEP_TO_MSS_BARS + MSS_TO_ENTRY_BARS
        ):
            state["sweep_index"] = None
            continue

        expected = int(state["direction"])

        if (
            int(fvg_direction.iloc[position]) == expected
            and pd.notna(fvg_top.iloc[position])
            and pd.notna(fvg_bottom.iloc[position])
        ):
            state["fvg_index"] = position
            state["fvg_top"] = float(fvg_top.iloc[position])
            state["fvg_bottom"] = float(fvg_bottom.iloc[position])

        structure = int(structure_events.iloc[position])

        if (
            structure == expected
            and position - int(state["sweep_index"]) <= SWEEP_TO_MSS_BARS
        ):
            state["mss_index"] = position

        if state["fvg_index"] is not None:
            if position - int(state["fvg_index"]) > FVG_MAX_AGE:
                state["fvg_index"] = None
                continue

            invalidated = (
                close.iloc[position] < state["fvg_bottom"]
                if expected == 1
                else close.iloc[position] > state["fvg_top"]
            )

            if invalidated:
                state["fvg_index"] = None
                continue

        if state["fvg_index"] is None:
            continue

        zone_touched = (
            low.iloc[position] <= state["fvg_top"]
            and high.iloc[position] >= state["fvg_bottom"]
        )

        if state["mss_index"] is None:
            if position > int(state["fvg_index"]) and zone_touched:
                state["fvg_index"] = None
            continue

        if position <= max(
            int(state["mss_index"]),
            int(state["fvg_index"]),
        ):
            if position > int(state["fvg_index"]) and zone_touched:
                state["fvg_index"] = None
            continue

        if position - int(state["mss_index"]) > MSS_TO_ENTRY_BARS:
            state["sweep_index"] = None
            continue

        touched = zone_touched
        midpoint = (state["fvg_top"] + state["fvg_bottom"]) / 2
        confirmed_rejection = (
            close.iloc[position] >= midpoint
            and close.iloc[position] > open_price.iloc[position]
            if expected == 1
            else close.iloc[position] <= midpoint
            and close.iloc[position] < open_price.iloc[position]
        )
        correct_bias = (
            bool(bullish_bias.iloc[position])
            if expected == 1
            else bool(bearish_bias.iloc[position])
        )

        if not (
            touched
            and confirmed_rejection
            and bool(state["sweep_location_valid"])
            and correct_bias
            and bool(active_session.iloc[position])
        ):
            continue

        risk_floor = max(
            float(atr.iloc[position]) * ATR_STOP_MULTIPLIER,
            float(close.iloc[position]) * MINIMUM_STOP_FRACTION,
        )
        entry = float(close.iloc[position])

        if expected == 1:
            stop = min(
                entry - risk_floor,
                float(state["sweep_extreme"])
                - float(atr.iloc[position]) * 0.10,
            )
            risk = entry - stop
            target, target_label = _nearest_target(
                pd.concat([source.iloc[position], data.iloc[position]]),
                direction=1,
                entry=entry,
                risk=risk,
            )
            long_signal.iloc[position] = True
            long_sl.iloc[position] = stop
            long_tp.iloc[position] = target
            sweep_type.iloc[position] = "Sell-side liquidity swept"
            sweep_location.iloc[position] = "Discount"
            signal_reason.iloc[position] = (
                "LONG: sell-side sweep → bullish MSS → bullish FVG pullback "
                "after a discount sweep during London/New York"
            )
        else:
            stop = max(
                entry + risk_floor,
                float(state["sweep_extreme"])
                + float(atr.iloc[position]) * 0.10,
            )
            risk = stop - entry
            target, target_label = _nearest_target(
                pd.concat([source.iloc[position], data.iloc[position]]),
                direction=-1,
                entry=entry,
                risk=risk,
            )
            short_signal.iloc[position] = True
            short_sl.iloc[position] = stop
            short_tp.iloc[position] = target
            sweep_type.iloc[position] = "Buy-side liquidity swept"
            sweep_location.iloc[position] = "Premium"
            signal_reason.iloc[position] = (
                "SHORT: buy-side sweep → bearish MSS → bearish FVG pullback "
                "after a premium sweep during London/New York"
            )

        setup_sweep_level.iloc[position] = state["sweep_level"]
        mss_direction.iloc[position] = expected
        zone_top.iloc[position] = state["fvg_top"]
        zone_bottom.iloc[position] = state["fvg_bottom"]
        target_basis.iloc[position] = target_label
        state["sweep_index"] = None

    data["LongSignal"] = long_signal.astype("int8")
    data["ShortSignal"] = short_signal.astype("int8")
    data["ExitLong"] = 0
    data["ExitShort"] = 0
    data["LongSL"] = long_sl
    data["LongTP"] = long_tp
    data["ShortSL"] = short_sl
    data["ShortTP"] = short_tp
    data["SweepType"] = sweep_type
    data["SweepLocation"] = sweep_location
    data["SweepLevel"] = setup_sweep_level
    data["MSSDirection"] = mss_direction
    data["ICTZoneTop"] = zone_top
    data["ICTZoneBottom"] = zone_bottom
    data["PriceLocation"] = np.select(
        [
            close.lt(data["Equilibrium"]),
            close.gt(data["Equilibrium"]),
        ],
        ["Discount", "Premium"],
        default="Unavailable",
    )
    data["ActiveSession"] = np.select(
        [london & new_york, london, new_york],
        ["London / New York overlap", "London", "New York"],
        default="Outside selected sessions",
    )
    data["TargetBasis"] = target_basis
    data["SignalReason"] = signal_reason
    return data
