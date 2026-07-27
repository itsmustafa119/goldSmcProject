"""Single entry point behind the two Windows batch launchers."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .selection import TIMEFRAME_CHOICES, select_market


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run market analysis or a strategy backtest."
    )
    parser.add_argument("mode", choices=("analysis", "backtest"))
    parser.add_argument("--symbol")
    parser.add_argument(
        "--timeframe",
        type=str.upper,
        choices=TIMEFRAME_CHOICES,
    )
    parser.add_argument("--strategy", type=Path)
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    symbol, timeframe = select_market(
        symbol=arguments.symbol,
        timeframe=arguments.timeframe,
    )
    os.environ["SMC_SYMBOL"] = symbol
    os.environ["SMC_TIMEFRAME"] = timeframe

    if arguments.no_browser:
        os.environ["SMC_NO_BROWSER"] = "1"

    if arguments.mode == "analysis" and arguments.once:
        os.environ["SMC_LIVE_MODE"] = "0"

    if arguments.mode == "analysis":
        from .config import acquire_instance_lock

        instance_lock = acquire_instance_lock()

        if instance_lock is None:
            raise RuntimeError(
                "The analysis dashboard is already running."
            )

        from .core import main as run_analysis

        run_analysis()
        return

    from .backtest_app import run_selected_backtest

    run_selected_backtest(
        symbol=symbol,
        timeframe=timeframe,
        strategy_path=arguments.strategy,
        use_cache=arguments.use_cache,
        no_browser=arguments.no_browser,
        once=arguments.once,
    )


if __name__ == "__main__":
    main()
