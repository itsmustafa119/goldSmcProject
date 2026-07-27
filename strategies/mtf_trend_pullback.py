"""Multi-market pullback entries aligned with completed higher timeframes."""

import pandas as pd

from gold_smc.strategy_templates import (
    crosses_above,
    crosses_below,
    directional_movement,
    ema,
    finalize_signals,
    price_frame,
    rsi,
)

STRATEGY_ID = "mtf_trend_pullback"
STRATEGY_NAME = "Multi-timeframe trend pullback"
STRATEGY_DESCRIPTION = (
    "Trades the selected execution timeframe only when two completed higher-"
    "timeframe EMA trends and slopes agree. A pullback must reclaim EMA20 "
    "without chasing an extended candle, with directional movement, ADX, and "
    "RSI confirmation. The stop is 1.25 ATR and the take-profit is 1.5 times "
    "the initial risk."
)
STRATEGY_EVIDENCE = (
    "This is a transparent short-horizon implementation of trend-following "
    "and time-series momentum with higher-timeframe confirmation."
)
STRATEGY_SOURCE_URL = (
    "https://www.aqr.com/Insights/Research/Journal-Article/"
    "A-Century-of-Evidence-on-Trend-Following-Investing"
)
BACKTEST_SETTINGS = {
    "cash": 500.0,
    "spread": 0.0001,
    "commission": 0.0,
    "margin": 0.002,
    "lot_size": 0.01,
    "contract_size": 100_000.0,
    "leverage": 500.0,
}
INDICATOR_COLUMNS = {
    "EMA20": "Execution EMA 20",
    "EMA100": "Execution EMA 100",
    "PlusDI14": "+DI (14)",
    "MinusDI14": "-DI (14)",
    "ADX14": "ADX (14)",
    "RSI14": "RSI (14)",
    "PullbackDistanceATR": "Distance from EMA20 (ATR)",
    "H1Close": "Completed first higher-timeframe close",
    "H1EMA50": "First higher-timeframe EMA 50",
    "H1EMA50Slope": "First higher-timeframe EMA 50 slope",
    "H4Close": "Completed second higher-timeframe close",
    "H4EMA50": "Second higher-timeframe EMA 50",
    "H4EMA50Slope": "Second higher-timeframe EMA 50 slope",
    "ATR": "ATR (14)",
}


def _completed_context(
    close: pd.Series,
    rule: str,
    period: int,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    higher_close = close.resample(
        rule,
        label="right",
        closed="left",
    ).last()
    higher_ema = ema(higher_close, period)
    return (
        higher_close.reindex(close.index, method="ffill"),
        higher_ema.reindex(close.index, method="ffill"),
        higher_ema.diff().reindex(close.index, method="ffill"),
    )


def _context_rules(index: pd.DatetimeIndex) -> tuple[str, str]:
    interval = index.to_series().diff().median()

    if pd.isna(interval) or interval <= pd.Timedelta(minutes=20):
        return "1h", "4h"

    if interval <= pd.Timedelta(minutes=90):
        return "4h", "1D"

    return "1D", "1W"


def prepare_strategy_data(results, *, swing_confirmation_bars):
    del swing_confirmation_bars
    data = price_frame(results)
    data["EMA20"] = ema(data["Close"], 20)
    data["EMA100"] = ema(data["Close"], 100)
    data["RSI14"] = rsi(data["Close"], 14)
    (
        data["PlusDI14"],
        data["MinusDI14"],
        data["ADX14"],
    ) = directional_movement(data, 14)
    first_rule, second_rule = _context_rules(data.index)
    (
        data["H1Close"],
        data["H1EMA50"],
        data["H1EMA50Slope"],
    ) = _completed_context(
        data["Close"],
        first_rule,
        50,
    )
    (
        data["H4Close"],
        data["H4EMA50"],
        data["H4EMA50Slope"],
    ) = _completed_context(
        data["Close"],
        second_rule,
        50,
    )
    data["PullbackDistanceATR"] = (
        (data["Close"] - data["EMA20"]).abs()
        / data["ATR"].where(data["ATR"].ne(0))
    )
    active_session = pd.Series(
        (data.index.hour >= 7) & (data.index.hour < 20),
        index=data.index,
    )
    bullish = (
        data["EMA20"].gt(data["EMA100"])
        & data["EMA20"].gt(data["EMA20"].shift(1))
        & data["H1Close"].gt(data["H1EMA50"])
        & data["H1EMA50Slope"].gt(0)
        & data["H4Close"].gt(data["H4EMA50"])
        & data["H4EMA50Slope"].gt(0)
    )
    bearish = (
        data["EMA20"].lt(data["EMA100"])
        & data["EMA20"].lt(data["EMA20"].shift(1))
        & data["H1Close"].lt(data["H1EMA50"])
        & data["H1EMA50Slope"].lt(0)
        & data["H4Close"].lt(data["H4EMA50"])
        & data["H4EMA50Slope"].lt(0)
    )
    long_signal = (
        active_session
        & bullish
        & data["ADX14"].ge(22)
        & data["PlusDI14"].gt(data["MinusDI14"])
        & data["RSI14"].between(45, 70)
        & data["PullbackDistanceATR"].le(0.35)
        & crosses_above(data["Close"], data["EMA20"])
    )
    short_signal = (
        active_session
        & bearish
        & data["ADX14"].ge(22)
        & data["MinusDI14"].gt(data["PlusDI14"])
        & data["RSI14"].between(30, 55)
        & data["PullbackDistanceATR"].le(0.35)
        & crosses_below(data["Close"], data["EMA20"])
    )
    long_stop = data["Close"] - data["ATR"] * 1.25
    short_stop = data["Close"] + data["ATR"] * 1.25
    no_exit = pd.Series(False, index=data.index)
    return finalize_signals(
        data,
        long_signal=long_signal,
        short_signal=short_signal,
        long_stop=long_stop,
        short_stop=short_stop,
        exit_long=no_exit,
        exit_short=no_exit,
        long_reason=(
            "LONG: non-extended execution EMA20 reclaim with rising bullish "
            "execution/HTF1/HTF2 trends, +DI, ADX and RSI confirmation"
        ),
        short_reason=(
            "SHORT: non-extended execution EMA20 loss with falling bearish "
            "execution/HTF1/HTF2 trends, -DI, ADX and RSI confirmation"
        ),
        risk_reward=1.5,
    )
