"""Causal SMC pullback strategy powered by backtesting.py.

This module deliberately shifts swing-derived fields by their confirmation
window before using them. That prevents a historical pivot from becoming
tradable before the future candles needed to confirm it have closed.
"""

from pathlib import Path

import numpy as np
import pandas as pd
from backtesting import Backtest, Strategy


class SMCBracketStrategy(Strategy):
    """Trade confirmed trend pullbacks with bracket SL/TP orders."""

    position_fraction = 0.10
    risk_reward = 2.0
    atr_multiplier = 1.5
    minimum_stop_fraction = 0.001

    def init(self) -> None:
        """The strategy consumes causal columns prepared beforehand."""

    def next(self) -> None:
        close = float(self.data.Close[-1])
        atr = float(self.data.ATR[-1])
        trend = int(self.data.TrendState[-1])

        if not np.isfinite(atr) or atr <= 0:
            return

        if self.position.is_long and trend == -1:
            self.position.close()
            return

        if self.position.is_short and trend == 1:
            self.position.close()
            return

        if self.position:
            return

        risk_floor = max(
            atr * self.atr_multiplier,
            close * self.minimum_stop_fraction,
        )

        if bool(self.data.LongSetup[-1]):
            range_low = float(self.data.RangeLow[-1])
            structural_stop = (
                range_low - atr * 0.10
                if np.isfinite(range_low)
                else close - risk_floor
            )
            stop_loss = min(close - risk_floor, structural_stop)
            risk = close - stop_loss
            take_profit = close + risk * self.risk_reward

            self.buy(
                size=self.position_fraction,
                sl=stop_loss,
                tp=take_profit,
                tag="SMC long pullback",
            )

        elif bool(self.data.ShortSetup[-1]):
            range_high = float(self.data.RangeHigh[-1])
            structural_stop = (
                range_high + atr * 0.10
                if np.isfinite(range_high)
                else close + risk_floor
            )
            stop_loss = max(close + risk_floor, structural_stop)
            risk = stop_loss - close
            take_profit = close - risk * self.risk_reward

            self.sell(
                size=self.position_fraction,
                sl=stop_loss,
                tp=take_profit,
                tag="SMC short pullback",
            )


def _structure_events(results: pd.DataFrame) -> pd.Series:
    """Move BOS/CHoCH signals to their actual confirmed break candles."""

    events = pd.Series(
        0,
        index=results.index,
        dtype="int8",
    )

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


def prepare_backtest_data(
    results: pd.DataFrame,
    *,
    swing_confirmation_bars: int,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Create the OHLCV and causal signal columns used by the strategy."""

    data = results.copy().reset_index(drop=True)
    data["time"] = pd.to_datetime(data["time"])

    structure_events = _structure_events(data)
    structure_bias = (
        structure_events
        .replace(0, np.nan)
        .ffill()
        .fillna(0)
        .astype("int8")
    )

    confirmed_state = data["Trend_State"].shift(
        swing_confirmation_bars
    )
    trend_state = confirmed_state.map(
        {
            "Uptrend": 1,
            "Downtrend": -1,
            "Transition / range": 0,
            "Insufficient structure": 0,
        }
    ).fillna(0).astype("int8")

    range_high = pd.to_numeric(
        data["Trend_RangeHigh"],
        errors="coerce",
    ).shift(swing_confirmation_bars)
    range_low = pd.to_numeric(
        data["Trend_RangeLow"],
        errors="coerce",
    ).shift(swing_confirmation_bars)
    equilibrium = pd.to_numeric(
        data["Trend_Equilibrium"],
        errors="coerce",
    ).shift(swing_confirmation_bars)

    close = data["close"].astype(float)
    inside_range = close.between(range_low, range_high)
    in_discount = inside_range & close.lt(equilibrium)
    in_premium = inside_range & close.gt(equilibrium)

    entered_discount = in_discount & ~in_discount.shift(
        fill_value=False
    )
    entered_premium = in_premium & ~in_premium.shift(
        fill_value=False
    )

    long_setup = (
        trend_state.eq(1)
        & structure_bias.eq(1)
        & entered_discount
    )
    short_setup = (
        trend_state.eq(-1)
        & structure_bias.eq(-1)
        & entered_premium
    )

    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            data["high"].astype(float) - data["low"].astype(float),
            (data["high"].astype(float) - previous_close).abs(),
            (data["low"].astype(float) - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(
        atr_period,
        min_periods=atr_period,
    ).mean()

    prepared = pd.DataFrame(
        {
            "Open": data["open"].astype(float).to_numpy(),
            "High": data["high"].astype(float).to_numpy(),
            "Low": data["low"].astype(float).to_numpy(),
            "Close": close.to_numpy(),
            "Volume": data["volume"].astype(float).to_numpy(),
            "LongSetup": long_setup.astype("int8").to_numpy(),
            "ShortSetup": short_setup.astype("int8").to_numpy(),
            "TrendState": trend_state.to_numpy(),
            "StructureBias": structure_bias.to_numpy(),
            "RangeLow": range_low.to_numpy(),
            "RangeHigh": range_high.to_numpy(),
            "Equilibrium": equilibrium.to_numpy(),
            "ATR": atr.to_numpy(),
        },
        index=pd.DatetimeIndex(data["time"], name="time"),
    )

    return prepared


def _number(stats: pd.Series, key: str):
    value = stats.get(key)

    if value is None or pd.isna(value):
        return None

    return float(value)


def run_smc_backtest(
    results: pd.DataFrame,
    *,
    output_file: str,
    trades_file: str,
    swing_confirmation_bars: int,
    cash: float = 100_000,
    spread: float = 0.0001,
    margin: float = 0.05,
    position_fraction: float = 0.10,
    risk_reward: float = 2.0,
    atr_multiplier: float = 1.5,
) -> dict:
    """Run the baseline strategy and save its interactive plot and trades."""

    prepared = prepare_backtest_data(
        results,
        swing_confirmation_bars=swing_confirmation_bars,
    )

    SMCBracketStrategy.position_fraction = position_fraction
    SMCBracketStrategy.risk_reward = risk_reward
    SMCBracketStrategy.atr_multiplier = atr_multiplier

    backtest = Backtest(
        prepared,
        SMCBracketStrategy,
        cash=cash,
        spread=spread,
        commission=0.0,
        margin=margin,
        trade_on_close=True,
        hedging=False,
        exclusive_orders=True,
        finalize_trades=True,
    )
    stats = backtest.run()

    output_path = Path(output_file).resolve()
    backtest.plot(
        results=stats,
        filename=str(output_path),
        plot_equity=True,
        plot_return=False,
        plot_pl=True,
        plot_volume=True,
        plot_drawdown=True,
        plot_trades=True,
        smooth_equity=False,
        relative_equity=True,
        superimpose=False,
        resample=False,
        open_browser=False,
    )

    trades = stats["_trades"].copy()

    if not trades.empty:
        trades.insert(
            0,
            "Direction",
            np.where(trades["Size"] > 0, "Long", "Short"),
        )
        trades["Outcome"] = np.where(
            trades["PnL"] > 0,
            "Win",
            np.where(trades["PnL"] < 0, "Loss", "Flat"),
        )

    trades_path = Path(trades_file).resolve()
    trades.to_csv(trades_path, index=False)

    recent_trades = []

    for _, trade in trades.tail(8).iloc[::-1].iterrows():
        recent_trades.append(
            {
                "direction": str(trade["Direction"]),
                "entry_time": str(trade["EntryTime"]),
                "exit_time": str(trade["ExitTime"]),
                "entry_price": float(trade["EntryPrice"]),
                "exit_price": float(trade["ExitPrice"]),
                "pnl": float(trade["PnL"]),
                "return_pct": float(trade["ReturnPct"]) * 100,
                "outcome": str(trade["Outcome"]),
            }
        )

    return {
        "start": str(stats.get("Start")),
        "end": str(stats.get("End")),
        "cash": float(cash),
        "spread": float(spread),
        "margin": float(margin),
        "position_fraction": float(position_fraction),
        "risk_reward": float(risk_reward),
        "atr_multiplier": float(atr_multiplier),
        "swing_confirmation_bars": int(swing_confirmation_bars),
        "return_pct": _number(stats, "Return [%]"),
        "buy_hold_return_pct": _number(stats, "Buy & Hold Return [%]"),
        "max_drawdown_pct": _number(stats, "Max. Drawdown [%]"),
        "win_rate_pct": _number(stats, "Win Rate [%]"),
        "profit_factor": _number(stats, "Profit Factor"),
        "expectancy_pct": _number(stats, "Expectancy [%]"),
        "equity_final": _number(stats, "Equity Final [$]"),
        "trades": int(stats.get("# Trades", 0)),
        "long_setups": int(prepared["LongSetup"].sum()),
        "short_setups": int(prepared["ShortSetup"].sum()),
        "recent_trades": recent_trades,
        "output_path": str(output_path),
        "trades_path": str(trades_path),
    }
