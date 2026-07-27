from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

from .config import SYMBOL, TIMEFRAME_NAME


TIMEFRAME = {
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
}[TIMEFRAME_NAME]


def find_matching_symbols(preferred: str = SYMBOL) -> list[str]:
    """Find possible broker variants of an instrument symbol."""

    symbols = mt5.symbols_get()

    if symbols is None:
        return []

    return [
        item.name
        for item in symbols
        if preferred.upper() in item.name.upper()
    ]


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
            f"Possible matching symbols: {find_matching_symbols(symbol)}"
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
    data = data.rename(columns={"tick_volume": "volume"})

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


def get_mt5_live_price(
    symbol: str,
    fallback_price: float,
) -> float:
    """Return the best available live MT5 price."""

    tick = mt5.symbol_info_tick(symbol)

    if tick is None:
        return float(fallback_price)

    for attribute in ["last", "bid", "ask"]:
        value = float(
            getattr(tick, attribute, 0) or 0
        )

        if value > 0:
            return value

    return float(fallback_price)
