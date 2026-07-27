"""Editable strategy rules for the standalone backtest service.

The service imports this file every time "Reload strategy" is pressed.  Keep
the column contract documented below, but feel free to change the signal,
stop-loss, take-profit, and exit rules.
"""

import numpy as np
import pandas as pd

from gold_smc.strategy_templates import prepare_smc_structure_data


STRATEGY_NAME = "Confirmed SMC pullback"
STRATEGY_DESCRIPTION = (
    "Trades with the confirmed M15 swing trend and confirmed BOS/CHoCH bias. "
    "A long requires price to enter discount; a short requires price to enter "
    "premium. The initial stop is outside the dealing range with a 1.5 ATR "
    "minimum distance, and the target is two times the initial risk."
)

# These assumptions are displayed in the report and used by the execution
# engine. A fractional position size of 0.10 means 10% of available liquidity.
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

RISK_REWARD = 2.0
ATR_MULTIPLIER = 1.5
MINIMUM_STOP_FRACTION = 0.001

# These values are copied into every trade's audit record. Add another
# prepared-data column here when a changed strategy uses another indicator.
INDICATOR_COLUMNS = {
    "TrendState": "Confirmed trend",
    "StructureBias": "Confirmed structure bias",
    "PriceLocation": "Dealing-range location",
    "RangeLow": "Confirmed range low",
    "Equilibrium": "Confirmed equilibrium",
    "RangeHigh": "Confirmed range high",
    "ATR": "ATR (14)",
}


def prepare_strategy_data(
    results: pd.DataFrame,
    *,
    swing_confirmation_bars: int,
) -> pd.DataFrame:
    """Return candles, entry/exit signals, and an initial bracket for each side.

    Required output columns:
      Open, High, Low, Close, Volume
      LongSignal, ShortSignal, ExitLong, ExitShort
      LongSL, LongTP, ShortSL, ShortTP
      SignalReason

    All signals must be causal: a row may only use information known at that
    row's close. ``prepare_smc_structure_data`` already delays swing-derived fields
    by the confirmation window and puts structure breaks on their break candle.
    """

    data = prepare_smc_structure_data(
        results,
        swing_confirmation_bars=swing_confirmation_bars,
    ).copy()

    close = data["Close"].astype(float)
    atr = data["ATR"].astype(float)
    risk_floor = pd.concat(
        [
            atr * ATR_MULTIPLIER,
            close * MINIMUM_STOP_FRACTION,
        ],
        axis=1,
    ).max(axis=1)

    long_structural_stop = (
        data["RangeLow"].astype(float) - atr * 0.10
    )
    short_structural_stop = (
        data["RangeHigh"].astype(float) + atr * 0.10
    )

    data["LongSL"] = pd.concat(
        [close - risk_floor, long_structural_stop],
        axis=1,
    ).min(axis=1)
    data["ShortSL"] = pd.concat(
        [close + risk_floor, short_structural_stop],
        axis=1,
    ).max(axis=1)

    long_risk = close - data["LongSL"]
    short_risk = data["ShortSL"] - close
    data["LongTP"] = close + long_risk * RISK_REWARD
    data["ShortTP"] = close - short_risk * RISK_REWARD

    data["LongSignal"] = data["LongSetup"].astype("int8")
    data["ShortSignal"] = data["ShortSetup"].astype("int8")

    # An open trade is closed when its confirmed trend reverses. The bracket
    # remains active until that happens.
    data["ExitLong"] = data["TrendState"].eq(-1).astype("int8")
    data["ExitShort"] = data["TrendState"].eq(1).astype("int8")

    data["PriceLocation"] = np.select(
        [
            close.lt(data["Equilibrium"]),
            close.gt(data["Equilibrium"]),
        ],
        ["Discount", "Premium"],
        default="Equilibrium / unavailable",
    )

    data["SignalReason"] = np.select(
        [
            data["LongSignal"].eq(1),
            data["ShortSignal"].eq(1),
        ],
        [
            (
                "LONG: confirmed uptrend + bullish structure bias + "
                "first close in discount"
            ),
            (
                "SHORT: confirmed downtrend + bearish structure bias + "
                "first close in premium"
            ),
        ],
        default="",
    )

    return data
