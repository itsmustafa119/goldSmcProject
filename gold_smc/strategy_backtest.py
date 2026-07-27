"""File-driven strategy execution and audit-data preparation."""

from __future__ import annotations

import importlib.util
import math
import warnings
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
from backtesting import Backtest, Strategy

from .config import SWING_LENGTH


REQUIRED_STRATEGY_COLUMNS = {
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "LongSignal",
    "ShortSignal",
    "ExitLong",
    "ExitShort",
    "LongSL",
    "LongTP",
    "ShortSL",
    "ShortTP",
    "SignalReason",
}

DEFAULT_BACKTEST_SETTINGS = {
    "cash": 100_000.0,
    "spread": 0.0001,
    "commission": 0.0,
    "margin": 0.05,
    "position_fraction": 0.10,
    "trade_on_close": True,
    "exclusive_orders": True,
    "finalize_trades": True,
}


class FileSignalStrategy(Strategy):
    """Execute the signal and bracket columns produced by a strategy file."""

    order_size = 0.10

    def init(self) -> None:
        """The imported strategy file prepares all causal signal columns."""

    @staticmethod
    def _valid_long_bracket(close: float, stop: float, target: float) -> bool:
        return (
            math.isfinite(close)
            and math.isfinite(stop)
            and math.isfinite(target)
            and stop < close < target
        )

    @staticmethod
    def _valid_short_bracket(close: float, stop: float, target: float) -> bool:
        return (
            math.isfinite(close)
            and math.isfinite(stop)
            and math.isfinite(target)
            and target < close < stop
        )

    def next(self) -> None:
        if self.position.is_long and bool(self.data.ExitLong[-1]):
            self.position.close()
            return

        if self.position.is_short and bool(self.data.ExitShort[-1]):
            self.position.close()
            return

        if self.position:
            return

        close = float(self.data.Close[-1])
        reason = str(self.data.SignalReason[-1])

        if bool(self.data.LongSignal[-1]):
            stop = float(self.data.LongSL[-1])
            target = float(self.data.LongTP[-1])

            if self._valid_long_bracket(close, stop, target):
                self.buy(
                    size=self.order_size,
                    sl=stop,
                    tp=target,
                    tag=reason or "Long signal",
                )

        elif bool(self.data.ShortSignal[-1]):
            stop = float(self.data.ShortSL[-1])
            target = float(self.data.ShortTP[-1])

            if self._valid_short_bracket(close, stop, target):
                self.sell(
                    size=self.order_size,
                    sl=stop,
                    tp=target,
                    tag=reason or "Short signal",
                )


def load_strategy_file(strategy_path: str | Path) -> ModuleType:
    """Import and validate an editable strategy Python file."""

    path = Path(strategy_path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"Strategy file was not found: {path}")

    if path.suffix.lower() != ".py":
        raise ValueError("The strategy file must be a Python (.py) file.")

    module_name = (
        f"_gold_smc_strategy_{path.stem}_{path.stat().st_mtime_ns}"
    )
    specification = importlib.util.spec_from_file_location(
        module_name,
        path,
    )

    if specification is None or specification.loader is None:
        raise ImportError(f"Could not import strategy file: {path}")

    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)

    if not callable(getattr(module, "prepare_strategy_data", None)):
        raise ValueError(
            "Strategy file must define prepare_strategy_data(results, "
            "swing_confirmation_bars=...)."
        )

    return module


def prepare_file_strategy(
    results: pd.DataFrame,
    strategy_module: ModuleType,
) -> pd.DataFrame:
    """Run the file's preparation function and enforce its data contract."""

    prepared = strategy_module.prepare_strategy_data(
        results.copy(),
        swing_confirmation_bars=SWING_LENGTH,
    )

    if not isinstance(prepared, pd.DataFrame):
        raise TypeError("prepare_strategy_data() must return a DataFrame.")

    missing = sorted(REQUIRED_STRATEGY_COLUMNS - set(prepared.columns))

    if missing:
        raise ValueError(
            "Strategy data is missing required columns: "
            + ", ".join(missing)
        )

    if not isinstance(prepared.index, pd.DatetimeIndex):
        raise ValueError("Strategy data must use a DatetimeIndex.")

    if not prepared.index.is_monotonic_increasing:
        prepared = prepared.sort_index()

    if prepared.index.has_duplicates:
        raise ValueError("Strategy data contains duplicate candle times.")

    return prepared


def _number(stats: pd.Series, key: str) -> float | None:
    value = stats.get(key)

    if value is None or pd.isna(value):
        return None

    return float(value)


def _format_indicator_value(value) -> str:
    if value is None or pd.isna(value):
        return "Unavailable"

    if isinstance(value, (float, np.floating)):
        return f"{float(value):,.4f}".rstrip("0").rstrip(".")

    if isinstance(value, (int, np.integer)):
        return str(int(value))

    return str(value)


def _entry_signal_row(
    prepared: pd.DataFrame,
    entry_bar: int,
    direction: str,
) -> tuple[int, pd.Series]:
    """Map a filled entry back to the signal row used by trade_on_close."""

    signal_column = (
        "LongSignal" if direction == "Long" else "ShortSignal"
    )

    for candidate in (entry_bar, entry_bar - 1):
        if 0 <= candidate < len(prepared):
            row = prepared.iloc[candidate]

            if bool(row[signal_column]):
                return candidate, row

    candidate = min(max(entry_bar, 0), len(prepared) - 1)
    return candidate, prepared.iloc[candidate]


def _infer_exit_reason(trade: pd.Series) -> str:
    exit_price = float(trade["ExitPrice"])
    stop = trade.get("InitialSL")
    target = trade.get("InitialTP")
    tolerance = max(abs(exit_price) * 0.00002, 1e-9)
    stop_distance = (
        abs(exit_price - float(stop))
        if pd.notna(stop)
        else float("inf")
    )
    target_distance = (
        abs(exit_price - float(target))
        if pd.notna(target)
        else float("inf")
    )

    if stop_distance <= tolerance and stop_distance <= target_distance:
        return "Stop loss"

    if target_distance <= tolerance:
        return "Take profit"

    return "Strategy exit / end of test"


def enrich_trades(
    trades: pd.DataFrame,
    prepared: pd.DataFrame,
    indicator_columns: dict[str, str],
) -> pd.DataFrame:
    """Attach the exact signal row, initial bracket, and indicator audit."""

    enriched = trades.copy()

    if enriched.empty:
        return pd.DataFrame(
            columns=[
                "Trade",
                "Direction",
                "EntryTime",
                "ExitTime",
                "EntryPrice",
                "ExitPrice",
                "InitialSL",
                "InitialTP",
                "PnL",
                "ReturnPct",
                "Outcome",
                "ExitReason",
                "SignalReason",
                "IndicatorContext",
            ]
        )

    enriched.insert(
        0,
        "Direction",
        np.where(enriched["Size"] > 0, "Long", "Short"),
    )
    enriched.insert(0, "Trade", range(1, len(enriched) + 1))

    initial_stops = []
    initial_targets = []
    signal_times = []
    signal_reasons = []
    indicator_contexts = []

    for _, trade in enriched.iterrows():
        direction = str(trade["Direction"])
        signal_bar, signal_row = _entry_signal_row(
            prepared,
            int(trade["EntryBar"]),
            direction,
        )
        prefix = "Long" if direction == "Long" else "Short"

        initial_stops.append(float(signal_row[f"{prefix}SL"]))
        initial_targets.append(float(signal_row[f"{prefix}TP"]))
        signal_times.append(prepared.index[signal_bar])
        signal_reasons.append(str(signal_row["SignalReason"]))

        context = []

        for column, label in indicator_columns.items():
            if column in signal_row.index:
                context.append(
                    f"{label}: {_format_indicator_value(signal_row[column])}"
                )

        indicator_contexts.append(" | ".join(context))

    enriched["SignalTime"] = signal_times
    enriched["InitialSL"] = initial_stops
    enriched["InitialTP"] = initial_targets
    enriched["SignalReason"] = signal_reasons
    enriched["IndicatorContext"] = indicator_contexts
    enriched["Outcome"] = np.select(
        [enriched["PnL"] > 0, enriched["PnL"] < 0],
        ["Win", "Loss"],
        default="Flat",
    )
    enriched["ExitReason"] = enriched.apply(
        _infer_exit_reason,
        axis=1,
    )

    return enriched


def run_file_backtest(
    results: pd.DataFrame,
    strategy_path: str | Path,
    *,
    setting_overrides: dict | None = None,
) -> dict:
    """Load a strategy file, run it, and return report-ready artifacts."""

    path = Path(strategy_path).resolve()
    module = load_strategy_file(path)
    prepared = prepare_file_strategy(results, module)

    settings = DEFAULT_BACKTEST_SETTINGS.copy()
    settings.update(getattr(module, "BACKTEST_SETTINGS", {}))

    if setting_overrides:
        settings.update(setting_overrides)

    position_fraction = float(settings.get("position_fraction", 0.10))
    lot_size = settings.get("lot_size")
    contract_size = float(settings.get("contract_size", 100.0))

    if lot_size is None:
        if not 0 < position_fraction < 1:
            raise ValueError("position_fraction must be between 0 and 1.")

        order_size: float | int = position_fraction
    else:
        units = float(lot_size) * contract_size
        rounded_units = int(round(units))

        if rounded_units < 1 or not math.isclose(
            units,
            rounded_units,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "lot_size × contract_size must produce at least one "
                "whole backtest unit."
            )

        order_size = rounded_units

    FileSignalStrategy.order_size = order_size
    with warnings.catch_warnings():
        if lot_size is not None:
            warnings.filterwarnings(
                "ignore",
                message="Some prices are larger than initial cash value.*",
            )

        backtest = Backtest(
            prepared,
            FileSignalStrategy,
            cash=float(settings["cash"]),
            spread=float(settings["spread"]),
            commission=float(settings["commission"]),
            margin=float(settings["margin"]),
            trade_on_close=bool(settings["trade_on_close"]),
            hedging=False,
            exclusive_orders=bool(settings["exclusive_orders"]),
            finalize_trades=bool(settings["finalize_trades"]),
        )
    stats = backtest.run()

    indicator_columns = dict(
        getattr(module, "INDICATOR_COLUMNS", {})
    )
    trades = enrich_trades(
        stats["_trades"],
        prepared,
        indicator_columns,
    )
    equity_curve = stats["_equity_curve"].copy()

    gross_profit = (
        float(trades.loc[trades["PnL"] > 0, "PnL"].sum())
        if not trades.empty
        else 0.0
    )
    gross_loss = (
        float(trades.loc[trades["PnL"] < 0, "PnL"].sum())
        if not trades.empty
        else 0.0
    )
    wins = int((trades["PnL"] > 0).sum()) if not trades.empty else 0
    losses = int((trades["PnL"] < 0).sum()) if not trades.empty else 0
    flats = int((trades["PnL"] == 0).sum()) if not trades.empty else 0

    cash = float(settings["cash"])
    final_equity = _number(stats, "Equity Final [$]") or cash

    return {
        "strategy_path": str(path),
        "strategy_name": str(
            getattr(module, "STRATEGY_NAME", path.stem)
        ),
        "strategy_description": str(
            getattr(module, "STRATEGY_DESCRIPTION", "")
        ),
        "settings": {
            **settings,
            "position_fraction": position_fraction,
            "effective_order_size": order_size,
        },
        "prepared": prepared,
        "trades": trades,
        "equity_curve": equity_curve,
        "summary": {
            "start": str(stats.get("Start")),
            "end": str(stats.get("End")),
            "starting_balance": cash,
            "final_balance": final_equity,
            "net_pnl": final_equity - cash,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "return_pct": _number(stats, "Return [%]"),
            "buy_hold_return_pct": _number(
                stats,
                "Buy & Hold Return [%]",
            ),
            "max_drawdown_pct": _number(
                stats,
                "Max. Drawdown [%]",
            ),
            "win_rate_pct": _number(stats, "Win Rate [%]"),
            "profit_factor": _number(stats, "Profit Factor"),
            "expectancy_pct": _number(stats, "Expectancy [%]"),
            "best_trade_pct": _number(stats, "Best Trade [%]"),
            "worst_trade_pct": _number(stats, "Worst Trade [%]"),
            "average_trade_pct": _number(stats, "Avg. Trade [%]"),
            "trades": int(stats.get("# Trades", 0)),
            "wins": wins,
            "losses": losses,
            "flat": flats,
            "long_signals": int(prepared["LongSignal"].fillna(0).sum()),
            "short_signals": int(prepared["ShortSignal"].fillna(0).sum()),
        },
    }
