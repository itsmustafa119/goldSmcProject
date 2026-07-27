"""Reusable causal indicators and bracket construction for strategy files."""

from __future__ import annotations

import numpy as np
import pandas as pd


def price_frame(results: pd.DataFrame) -> pd.DataFrame:
    """Return a causal OHLCV frame indexed by candle close time."""

    source = results.copy()
    source["time"] = pd.to_datetime(source["time"])
    data = pd.DataFrame(
        {
            "Open": pd.to_numeric(
                source["open"],
                errors="coerce",
            ).to_numpy(),
            "High": pd.to_numeric(
                source["high"],
                errors="coerce",
            ).to_numpy(),
            "Low": pd.to_numeric(
                source["low"],
                errors="coerce",
            ).to_numpy(),
            "Close": pd.to_numeric(
                source["close"],
                errors="coerce",
            ).to_numpy(),
            "Volume": pd.to_numeric(
                source["volume"],
                errors="coerce",
            ).to_numpy(),
        },
        index=pd.DatetimeIndex(source["time"], name="time"),
    )
    data["ATR"] = atr(data, 14)
    return data


def _confirmed_structure_events(results: pd.DataFrame) -> pd.Series:
    """Move BOS and CHoCH rows to their confirmed break candles."""

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
        events.at[broken_index] = (
            int(choch)
            if pd.notna(choch) and float(choch) != 0
            else int(bos)
        )

    return events


def prepare_smc_structure_data(
    results: pd.DataFrame,
    *,
    swing_confirmation_bars: int,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Create causal SMC structure and pullback columns for strategies.

    Swing-derived fields are delayed by their confirmation window so a
    historical pivot never becomes available before the confirming candles
    have closed.
    """

    source = results.copy().reset_index(drop=True)
    source["time"] = pd.to_datetime(source["time"])

    structure_bias = (
        _confirmed_structure_events(source)
        .replace(0, np.nan)
        .ffill()
        .fillna(0)
        .astype("int8")
    )
    trend_state = (
        source["Trend_State"]
        .shift(swing_confirmation_bars)
        .map(
            {
                "Uptrend": 1,
                "Downtrend": -1,
                "Transition / range": 0,
                "Insufficient structure": 0,
            }
        )
        .fillna(0)
        .astype("int8")
    )
    range_high = pd.to_numeric(
        source["Trend_RangeHigh"],
        errors="coerce",
    ).shift(swing_confirmation_bars)
    range_low = pd.to_numeric(
        source["Trend_RangeLow"],
        errors="coerce",
    ).shift(swing_confirmation_bars)
    equilibrium = pd.to_numeric(
        source["Trend_Equilibrium"],
        errors="coerce",
    ).shift(swing_confirmation_bars)

    close = source["close"].astype(float)
    inside_range = close.between(range_low, range_high)
    in_discount = inside_range & close.lt(equilibrium)
    in_premium = inside_range & close.gt(equilibrium)
    long_setup = (
        trend_state.eq(1)
        & structure_bias.eq(1)
        & in_discount
        & ~in_discount.shift(fill_value=False)
    )
    short_setup = (
        trend_state.eq(-1)
        & structure_bias.eq(-1)
        & in_premium
        & ~in_premium.shift(fill_value=False)
    )

    data = price_frame(source)
    data["ATR"] = true_range(data).rolling(
        atr_period,
        min_periods=atr_period,
    ).mean()
    data["LongSetup"] = long_setup.astype("int8").to_numpy()
    data["ShortSetup"] = short_setup.astype("int8").to_numpy()
    data["TrendState"] = trend_state.to_numpy()
    data["StructureBias"] = structure_bias.to_numpy()
    data["RangeLow"] = range_low.to_numpy()
    data["RangeHigh"] = range_high.to_numpy()
    data["Equilibrium"] = equilibrium.to_numpy()
    return data


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(
        span=period,
        adjust=False,
        min_periods=period,
    ).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def true_range(data: pd.DataFrame) -> pd.Series:
    previous_close = data["Close"].shift(1)
    return pd.concat(
        [
            data["High"] - data["Low"],
            (data["High"] - previous_close).abs(),
            (data["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(data).ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    change = series.diff()
    gains = change.clip(lower=0)
    losses = -change.clip(upper=0)
    average_gain = gains.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()
    average_loss = losses.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()
    relative_strength = average_gain / average_loss.replace(0, np.nan)
    output = 100 - (100 / (1 + relative_strength))
    return output.where(average_loss.ne(0), 100.0)


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    line = ema(series, fast) - ema(series, slow)
    signal_line = ema(line, signal)
    return line, signal_line, line - signal_line


def stochastic(
    data: pd.DataFrame,
    period: int = 14,
    smooth: int = 3,
) -> tuple[pd.Series, pd.Series]:
    lowest = data["Low"].rolling(period, min_periods=period).min()
    highest = data["High"].rolling(period, min_periods=period).max()
    denominator = (highest - lowest).replace(0, np.nan)
    percent_k = 100 * (data["Close"] - lowest) / denominator
    percent_d = percent_k.rolling(
        smooth,
        min_periods=smooth,
    ).mean()
    return percent_k, percent_d


def williams_r(
    data: pd.DataFrame,
    period: int = 14,
) -> pd.Series:
    """Return Williams %R on the conventional -100 to 0 scale."""

    lowest = data["Low"].rolling(period, min_periods=period).min()
    highest = data["High"].rolling(period, min_periods=period).max()
    denominator = (highest - lowest).replace(0, np.nan)
    return -100 * (highest - data["Close"]) / denominator


def completed_timeframe(
    series: pd.Series,
    rule: str,
    *,
    aggregation: str = "last",
) -> pd.Series:
    """Map only completed higher-timeframe values back to the source index.

    M15 timestamps represent candle opens. A value labelled at 13:00 for a
    one-hour rule therefore contains only the 12:00 through 12:45 candles and
    is safe to use from 13:00 onward.
    """

    resampler = series.resample(
        rule,
        label="right",
        closed="left",
    )

    if aggregation == "last":
        higher = resampler.last()
    elif aggregation == "max":
        higher = resampler.max()
    elif aggregation == "min":
        higher = resampler.min()
    elif aggregation == "sum":
        higher = resampler.sum()
    else:
        raise ValueError(f"Unsupported aggregation: {aggregation}")

    return higher.reindex(series.index, method="ffill")


def directional_movement(
    data: pd.DataFrame,
    period: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    up_move = data["High"].diff()
    down_move = -data["Low"].diff()
    plus_dm = pd.Series(
        np.where(
            (up_move > down_move) & (up_move > 0),
            up_move,
            0.0,
        ),
        index=data.index,
    )
    minus_dm = pd.Series(
        np.where(
            (down_move > up_move) & (down_move > 0),
            down_move,
            0.0,
        ),
        index=data.index,
    )
    smoothed_tr = true_range(data).ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()
    plus_di = 100 * plus_dm.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean() / smoothed_tr
    minus_di = 100 * minus_dm.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean() / smoothed_tr
    denominator = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / denominator
    adx = dx.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()
    return plus_di, minus_di, adx


def crosses_above(left: pd.Series, right) -> pd.Series:
    right_series = (
        right
        if isinstance(right, pd.Series)
        else pd.Series(right, index=left.index)
    )
    return left.gt(right_series) & left.shift(1).le(right_series.shift(1))


def crosses_below(left: pd.Series, right) -> pd.Series:
    right_series = (
        right
        if isinstance(right, pd.Series)
        else pd.Series(right, index=left.index)
    )
    return left.lt(right_series) & left.shift(1).ge(right_series.shift(1))


def lower_stop(
    close: pd.Series,
    atr_values: pd.Series,
    structural_level: pd.Series | None = None,
    *,
    atr_multiple: float = 1.5,
) -> pd.Series:
    candidates = [close - atr_values * atr_multiple]

    if structural_level is not None:
        candidates.append(structural_level)

    return pd.concat(candidates, axis=1).min(axis=1)


def upper_stop(
    close: pd.Series,
    atr_values: pd.Series,
    structural_level: pd.Series | None = None,
    *,
    atr_multiple: float = 1.5,
) -> pd.Series:
    candidates = [close + atr_values * atr_multiple]

    if structural_level is not None:
        candidates.append(structural_level)

    return pd.concat(candidates, axis=1).max(axis=1)


def finalize_signals(
    data: pd.DataFrame,
    *,
    long_signal: pd.Series,
    short_signal: pd.Series,
    long_stop: pd.Series,
    short_stop: pd.Series,
    exit_long: pd.Series,
    exit_short: pd.Series,
    long_reason: str,
    short_reason: str,
    risk_reward: float,
) -> pd.DataFrame:
    """Attach the standard file-strategy contract without future data."""

    close = data["Close"].astype(float)
    long_valid = (
        long_signal.fillna(False)
        & long_stop.notna()
        & long_stop.lt(close)
        & data["ATR"].gt(0)
    )
    short_valid = (
        short_signal.fillna(False)
        & short_stop.notna()
        & short_stop.gt(close)
        & data["ATR"].gt(0)
    )

    data["LongSignal"] = long_valid.astype("int8")
    data["ShortSignal"] = short_valid.astype("int8")
    data["ExitLong"] = exit_long.fillna(False).astype("int8")
    data["ExitShort"] = exit_short.fillna(False).astype("int8")
    data["LongSL"] = long_stop
    data["ShortSL"] = short_stop
    data["LongTP"] = close + (close - long_stop) * risk_reward
    data["ShortTP"] = close - (short_stop - close) * risk_reward
    data["SignalReason"] = np.select(
        [long_valid, short_valid],
        [long_reason, short_reason],
        default="",
    )
    return data
