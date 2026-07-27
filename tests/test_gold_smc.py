import unittest
from unittest.mock import patch

import pandas as pd
import plotly.graph_objects as go

from gold_smc.backtest_app import discover_strategy_files
from gold_smc.chart import (
    add_backtest_trade_overlays,
    format_compact_number,
)
from gold_smc.config import PROJECT_ROOT, project_path
from gold_smc.indicators import calculate_structure_map
from gold_smc.selection import (
    choose_symbol_interactively,
    choose_timeframe_interactively,
)
from gold_smc.strategy_backtest import (
    REQUIRED_STRATEGY_COLUMNS,
    load_strategy_file,
    prepare_file_strategy,
)
from gold_smc.strategy_templates import price_frame


class GoldSmcTestCase(unittest.TestCase):
    def test_project_path_resolves_relative_path(self):
        resolved = project_path("test-file.txt")

        self.assertTrue(resolved.is_absolute())
        self.assertTrue(str(resolved).endswith("test-file.txt"))

    def test_format_compact_number(self):
        self.assertEqual(format_compact_number(123), "123")
        self.assertEqual(format_compact_number(1234), "1.23k")
        self.assertEqual(format_compact_number(1234567), "1.23M")

    def test_calculate_structure_map_returns_columns(self):
        data = pd.DataFrame(
            {
                "time": pd.date_range(
                    "2026-01-01",
                    periods=4,
                    freq="15min",
                ),
                "open": [100.0, 101.0, 102.0, 101.0],
                "high": [101.0, 102.0, 103.0, 102.0],
                "low": [99.0, 100.0, 101.0, 100.0],
                "close": [100.5, 101.5, 102.5, 101.5],
                "volume": [100, 110, 120, 130],
            }
        )
        swings = pd.DataFrame(
            {
                "HighLow": [1, -1, 1, -1],
                "Level": [101.0, 100.0, 102.0, 100.0],
            },
            index=data.index,
        )

        structure = calculate_structure_map(data, swings)

        self.assertEqual(len(structure), len(data))
        self.assertIn("Support", structure.columns)
        self.assertIn("Resistance", structure.columns)
        self.assertIn("Zone", structure.columns)

    def test_confirmed_smc_strategy_produces_auditable_brackets(self):
        periods = 80
        close = [103.0] * periods
        close[30:] = [101.0] * (periods - 30)
        results = pd.DataFrame(
            {
                "time": pd.date_range(
                    "2026-01-01",
                    periods=periods,
                    freq="15min",
                ),
                "open": close,
                "high": [value + 1 for value in close],
                "low": [value - 1 for value in close],
                "close": close,
                "volume": [1000] * periods,
                "Trend_State": ["Uptrend"] * periods,
                "Trend_RangeHigh": [105.0] * periods,
                "Trend_RangeLow": [99.0] * periods,
                "Trend_Equilibrium": [102.0] * periods,
                "Structure_BOS": [0.0] * periods,
                "Structure_CHOCH": [0.0] * periods,
                "Structure_BrokenIndex": [float("nan")] * periods,
            }
        )
        results.loc[20, "Structure_BOS"] = 1.0
        results.loc[20, "Structure_BrokenIndex"] = 25.0
        strategy = load_strategy_file(
            PROJECT_ROOT / "strategies" / "confirmed_smc_pullback.py"
        )

        prepared = prepare_file_strategy(results, strategy)

        self.assertTrue(
            REQUIRED_STRATEGY_COLUMNS.issubset(prepared.columns)
        )
        entries = prepared[prepared["LongSignal"].eq(1)]
        self.assertFalse(entries.empty)
        self.assertTrue((entries["LongSL"] < entries["Close"]).all())
        self.assertTrue((entries["LongTP"] > entries["Close"]).all())

    def test_ict_moves_liquidity_to_actual_sweep_candle(self):
        strategy = load_strategy_file(
            PROJECT_ROOT / "strategies" / "ict_liquidity_fvg.py"
        )
        results = pd.DataFrame(
            {
                "Liquidity_Liquidity": [0, 1, 0, 0, 0, 0],
                "Liquidity_Level": [float("nan"), 2_400.0]
                + [float("nan")] * 4,
                "Liquidity_Swept": [0, 5, 0, 0, 0, 0],
            }
        )

        direction, level = strategy._liquidity_sweep_events(results)

        self.assertEqual(int(direction.iloc[1]), 0)
        self.assertEqual(int(direction.iloc[5]), 1)
        self.assertEqual(float(level.iloc[5]), 2_400.0)

    def test_ict_moves_structure_to_actual_break_candle(self):
        strategy = load_strategy_file(
            PROJECT_ROOT / "strategies" / "ict_liquidity_fvg.py"
        )
        results = pd.DataFrame(
            {
                "Structure_BOS": [0, -1, 0, 0, 0],
                "Structure_CHOCH": [0, 0, 0, 0, 0],
                "Structure_BrokenIndex": [
                    float("nan"),
                    4,
                    float("nan"),
                    float("nan"),
                    float("nan"),
                ],
            }
        )

        events = strategy._confirmed_structure_events(results)

        self.assertEqual(int(events.iloc[1]), 0)
        self.assertEqual(int(events.iloc[4]), -1)

    def test_price_frame_preserves_ohlcv_values(self):
        source = pd.DataFrame(
            {
                "time": pd.date_range(
                    "2026-01-01",
                    periods=20,
                    freq="15min",
                ),
                "open": [100.0] * 20,
                "high": [101.0] * 20,
                "low": [99.0] * 20,
                "close": [100.5] * 20,
                "volume": [1000] * 20,
            }
        )

        prepared = price_frame(source)

        self.assertFalse(
            prepared[["Open", "High", "Low", "Close", "Volume"]]
            .isna()
            .any()
            .any()
        )

    def test_only_three_clear_strategies_are_discoverable(self):
        strategies = discover_strategy_files()

        self.assertEqual(len(strategies), 3)
        self.assertTrue(strategies[0]["recommended"])
        self.assertEqual(
            {
                item["path"].name for item in strategies
            },
            {
                "confirmed_smc_pullback.py",
                "ict_liquidity_fvg.py",
                "mtf_trend_pullback.py",
            },
        )

    def test_trade_overlay_includes_and_numbers_every_trade(self):
        trades = pd.DataFrame(
            {
                "Trade": [1, 2],
                "Direction": ["Long", "Short"],
                "EntryTime": pd.to_datetime(
                    ["2026-01-01 10:00", "2026-01-02 10:00"]
                ),
                "EntryPrice": [100.0, 110.0],
                "InitialSL": [98.0, 112.0],
                "InitialTP": [104.0, 106.0],
                "ExitTime": pd.to_datetime(
                    ["2026-01-01 11:00", "2026-01-02 12:00"]
                ),
                "ExitPrice": [104.0, 112.0],
                "PnL": [20.0, -10.0],
                "Outcome": ["Win", "Loss"],
                "ExitReason": ["Take profit", "Stop loss"],
                "SignalReason": ["Long rule", "Short rule"],
                "IndicatorContext": ["RSI=55", "RSI=45"],
                "ReturnPct": [0.02, -0.01],
            }
        )
        figure = go.Figure()

        add_backtest_trade_overlays(
            figure,
            trades,
            first_time=pd.Timestamp("2026-01-01"),
            last_time=pd.Timestamp("2026-01-03"),
        )

        entry_traces = [
            trace
            for trace in figure.data
            if trace.name in {"Long entries", "Short entries"}
        ]
        exit_traces = [
            trace
            for trace in figure.data
            if trace.name in {"Win exits", "Loss exits"}
        ]
        entry_labels = [
            label
            for trace in entry_traces
            for label in trace.text
        ]
        exit_labels = [
            label
            for trace in exit_traces
            for label in trace.text
        ]

        self.assertEqual(entry_labels, ["#1", "#2"])
        self.assertEqual(exit_labels, ["#1", "#2"])

    def test_timeframe_prompt(self):
        with patch("builtins.input", return_value="2"):
            self.assertEqual(choose_timeframe_interactively(), "H1")

        with patch("builtins.input", return_value="H4"):
            self.assertEqual(choose_timeframe_interactively(), "H4")

        with patch("builtins.input", return_value=""):
            self.assertEqual(choose_timeframe_interactively(), "M15")

    def test_symbol_prompt(self):
        with patch("builtins.input", return_value="2"):
            self.assertEqual(choose_symbol_interactively(), "EURUSD")

        with patch("builtins.input", return_value=""):
            self.assertEqual(choose_symbol_interactively(), "XAUUSD")


if __name__ == "__main__":
    unittest.main()
