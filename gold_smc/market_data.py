"""Fresh or cached multi-timeframe candles for strategy backtesting."""

from __future__ import annotations

from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

from .config import OUTPUT_DIRECTORY
from .indicators import calculate_smc_indicators
from .mt5_client import get_mt5_candles
from .selection import resolve_mt5_symbol


DEFAULT_SYMBOL = "XAUUSD"
TIMEFRAMES = {
    "M15": (mt5.TIMEFRAME_M15, 10_000),
    "H1": (mt5.TIMEFRAME_H1, 3_000),
    "H4": (mt5.TIMEFRAME_H4, 1_500),
}


def _cache_path(symbol: str, timeframe_name: str) -> Path:
    safe_symbol = "".join(
        character.lower()
        for character in symbol
        if character.isalnum()
    )
    return OUTPUT_DIRECTORY / f"{safe_symbol}_{timeframe_name.lower()}.csv"


def _read_cache(path: Path) -> pd.DataFrame:
    data = pd.read_csv(path)
    data["time"] = pd.to_datetime(data["time"])
    return data


def _write_cache(path: Path, data: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    data.to_csv(temporary, index=False)
    temporary.replace(path)


def load_market_timeframes(
    *,
    refresh_data: bool,
    preferred_symbol: str = DEFAULT_SYMBOL,
) -> tuple[dict[str, pd.DataFrame], str, str, float]:
    """Load M15/H1/H4 candles and enrich the selected market data."""

    if refresh_data:
        if not mt5.initialize():
            raise RuntimeError(
                "Could not connect to MetaTrader 5. "
                f"MetaTrader error: {mt5.last_error()}"
            )

        try:
            symbol = resolve_mt5_symbol(preferred_symbol)
            symbol_info = mt5.symbol_info(symbol)
            contract_size = float(
                getattr(symbol_info, "trade_contract_size", 0) or 0
            )
            frames = {}

            for name, (timeframe, count) in TIMEFRAMES.items():
                candles = get_mt5_candles(
                    symbol=symbol,
                    timeframe=timeframe,
                    candle_count=count,
                )
                frames[name] = candles
                _write_cache(_cache_path(symbol, name), candles)
        finally:
            mt5.shutdown()

        source = (
            f"fresh MetaTrader 5 {symbol} candles "
            f"({len(frames['M15']):,} M15, "
            f"{len(frames['H1']):,} H1, "
            f"{len(frames['H4']):,} H4)"
        )
    else:
        symbol = preferred_symbol
        contract_size = (
            100.0
            if "XAU" in symbol.upper() or "GOLD" in symbol.upper()
            else 100_000.0
        )
        cache_paths = {
            name: _cache_path(symbol, name)
            for name in TIMEFRAMES
        }

        if not all(path.exists() for path in cache_paths.values()):
            return load_market_timeframes(
                refresh_data=True,
                preferred_symbol=preferred_symbol,
            )

        frames = {
            name: _read_cache(path)
            for name, path in cache_paths.items()
        }
        source = (
            f"cached MT5 {symbol} candles "
            f"({len(frames['M15']):,} M15, "
            f"{len(frames['H1']):,} H1, "
            f"{len(frames['H4']):,} H4)"
        )

    raw_m15 = frames["M15"]
    frames["M15"] = calculate_smc_indicators(raw_m15)
    if contract_size <= 0:
        contract_size = 100.0

    return frames, source, symbol, contract_size
