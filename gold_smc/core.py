import time
import webbrowser
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

from .chart import create_interactive_chart, create_mplfinance_snapshot, latest_value
from .config import (
    BACKTEST_ATR_MULTIPLIER,
    BACKTEST_CASH,
    BACKTEST_MARGIN,
    BACKTEST_OUTPUT_FILE,
    BACKTEST_POSITION_FRACTION,
    BACKTEST_RISK_REWARD,
    BACKTEST_SPREAD,
    BACKTEST_TRADES_FILE,
    CHART_CANDLES,
    CSV_OUTPUT_FILE,
    HTML_OUTPUT_FILE,
    LIVE_MODE,
    LIVE_REFRESH_SECONDS,
    MPLFINANCE_CANDLES,
    MPLFINANCE_OUTPUT_FILE,
    NUMBER_OF_CANDLES,
    SWING_LENGTH,
    SYMBOL,
    project_path,
)
from .dashboard import start_live_dashboard_server, update_live_state
from .indicators import calculate_smc_indicators
from .mt5_client import TIMEFRAME, get_mt5_candles, get_mt5_live_price
import smc_backtest


def count_signals(
    results: pd.DataFrame,
    column: str,
) -> int:
    if column not in results.columns:
        return 0

    return int(
        results[column]
        .fillna(0)
        .ne(0)
        .sum()
    )


def print_summary(
    results: pd.DataFrame,
) -> None:
    latest = results.iloc[-1]

    print("\n========================================")
    print("LATEST XAUUSD DATA")
    print("========================================")
    print(f"Time:  {latest['time']}")
    print(f"Close: {latest['close']:.2f}")

    previous_high = latest_value(
        results,
        "Daily_PreviousHigh",
    )
    previous_low = latest_value(
        results,
        "Daily_PreviousLow",
    )

    if previous_high is not None:
        print(
            f"Previous daily high: "
            f"{float(previous_high):.2f}"
        )

    if previous_low is not None:
        print(
            f"Previous daily low:  "
            f"{float(previous_low):.2f}"
        )

    print("\n========================================")
    print("INDICATOR SUMMARY")
    print("========================================")
    print(
        f"Fair Value Gaps: "
        f"{count_signals(results, 'FVG_FVG')}"
    )
    print(
        f"BOS signals:     "
        f"{count_signals(results, 'Structure_BOS')}"
    )
    print(
        f"CHoCH signals:   "
        f"{count_signals(results, 'Structure_CHOCH')}"
    )
    print(
        f"Liquidity zones: "
        f"{count_signals(results, 'Liquidity_Liquidity')}"
    )
    print(
        f"Order blocks:    "
        f"{count_signals(results, 'OB_OB')}"
    )
    print(
        f"Swing trend:     "
        f"{latest.get('Trend_State', 'Unavailable')}"
    )
    print(
        f"Dealing range:   "
        f"{latest.get('Trend_Zone', 'Unavailable')}"
    )


def analyze_and_write_outputs(
    candles: pd.DataFrame,
) -> tuple[pd.DataFrame, Path, Path]:
    results = calculate_smc_indicators(
        candles
    )

    csv_path = project_path(
        CSV_OUTPUT_FILE
    ).resolve()
    temporary_csv_path = csv_path.with_suffix(
        ".tmp"
    )
    results.to_csv(
        temporary_csv_path,
        index=False,
    )

    try:
        temporary_csv_path.replace(
            csv_path
        )
    except PermissionError:
        results.to_csv(
            csv_path,
            index=False,
        )
        temporary_csv_path.unlink(
            missing_ok=True
        )

    backtest_summary = None

    try:
        backtest_summary = smc_backtest.run_smc_backtest(
            results,
            output_file=BACKTEST_OUTPUT_FILE,
            trades_file=BACKTEST_TRADES_FILE,
            swing_confirmation_bars=SWING_LENGTH,
            cash=BACKTEST_CASH,
            spread=BACKTEST_SPREAD,
            margin=BACKTEST_MARGIN,
            position_fraction=BACKTEST_POSITION_FRACTION,
            risk_reward=BACKTEST_RISK_REWARD,
            atr_multiplier=BACKTEST_ATR_MULTIPLIER,
        )
    except Exception as backtest_error:
        print(
            "Backtest warning: "
            f"{backtest_error}"
        )

    chart_path = create_interactive_chart(
        results=results,
        number_of_candles=CHART_CANDLES,
        backtest_summary=backtest_summary,
    )

    create_mplfinance_snapshot(
        results=results,
        number_of_candles=MPLFINANCE_CANDLES,
    )

    return results, csv_path, chart_path


def main() -> None:
    print("Connecting to MetaTrader 5...")

    if not mt5.initialize():
        raise RuntimeError(
            "Could not connect to MetaTrader 5.\n"
            f"MetaTrader error: {mt5.last_error()}"
        )

    live_server = None

    try:
        print(
            f"Downloading {NUMBER_OF_CANDLES} completed "
            f"{SYMBOL} M15 candles..."
        )

        candles = get_mt5_candles(
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            candle_count=NUMBER_OF_CANDLES,
        )

        print(
            f"Downloaded {len(candles)} candles."
        )

        print(
            "Calculating indicators and creating the dashboard..."
        )

        results, csv_path, chart_path = (
            analyze_and_write_outputs(
                candles
            )
        )

        print_summary(results)

        print("\n========================================")
        print("FILES CREATED")
        print("========================================")
        print(f"CSV file:   {csv_path}")
        print(f"Chart file: {chart_path}")
        print(
            "MPL chart:  "
            f"{project_path(MPLFINANCE_OUTPUT_FILE).resolve()}"
        )
        print(
            "Backtest:   "
            f"{project_path(BACKTEST_OUTPUT_FILE).resolve()}"
        )
        print(
            "Trades:     "
            f"{project_path(BACKTEST_TRADES_FILE).resolve()}"
        )

        last_candle_time = pd.Timestamp(
            results["time"].iloc[-1]
        )
        fallback_price = float(
            results["close"].iloc[-1]
        )
        live_price = get_mt5_live_price(
            SYMBOL,
            fallback_price,
        )

        update_live_state(
            last_candle_time=last_candle_time,
            last_price=live_price,
            increment_version=True,
        )

        if not LIVE_MODE:
            print("\nOpening static chart in your browser...")
            webbrowser.open_new_tab(
                chart_path.as_uri()
            )
            return

        live_server, dashboard_url = (
            start_live_dashboard_server()
        )

        print("\n========================================")
        print("LIVE DASHBOARD")
        print("========================================")
        print(f"URL: {dashboard_url}")
        print(
            f"Checking MT5 every {LIVE_REFRESH_SECONDS} seconds."
        )
        print(
            "The chart recalculates after each completed "
            f"M15 candle. Press Ctrl+C to stop."
        )

        webbrowser.open_new_tab(
            dashboard_url
        )

        last_error_message = None

        while True:
            time.sleep(
                LIVE_REFRESH_SECONDS
            )

            try:
                recent_candles = get_mt5_candles(
                    symbol=SYMBOL,
                    timeframe=mt5.TIMEFRAME_M15,
                    candle_count=2,
                )

                newest_candle_time = pd.Timestamp(
                    recent_candles["time"].iloc[-1]
                )

                live_price = get_mt5_live_price(
                    SYMBOL,
                    fallback_price,
                )

                if newest_candle_time > last_candle_time:
                    print(
                        "\nNew completed candle detected: "
                        f"{newest_candle_time}"
                    )

                    candles = get_mt5_candles(
                        symbol=SYMBOL,
                        timeframe=TIMEFRAME,
                        candle_count=NUMBER_OF_CANDLES,
                    )

                    results, csv_path, chart_path = (
                        analyze_and_write_outputs(
                            candles
                        )
                    )

                    last_candle_time = pd.Timestamp(
                        results["time"].iloc[-1]
                    )
                    fallback_price = float(
                        results["close"].iloc[-1]
                    )

                    update_live_state(
                        last_candle_time=last_candle_time,
                        last_price=live_price,
                        increment_version=True,
                    )

                    print(
                        "Dashboard refreshed and all SMC "
                        "indicators recalculated."
                    )
                else:
                    update_live_state(
                        last_candle_time=last_candle_time,
                        last_price=live_price,
                    )

                if last_error_message is not None:
                    print("MT5 live connection recovered.")
                    last_error_message = None

            except Exception as live_error:
                error_message = str(live_error)

                update_live_state(
                    last_candle_time=last_candle_time,
                    last_price=live_price,
                    error=error_message,
                )

                if error_message != last_error_message:
                    print(
                        "\nLive update warning: "
                        f"{error_message}"
                    )
                    last_error_message = error_message

    except KeyboardInterrupt:
        print("\nLive dashboard stopped by user.")

    except Exception as error:
        print(f"\nError: {error}")

    finally:
        if live_server is not None:
            live_server.shutdown()
            live_server.server_close()

        mt5.shutdown()
        print(
            "\nMetaTrader 5 connection closed."
        )
