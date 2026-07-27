"""Shared terminal selection for MT5 instrument and timeframe."""

from __future__ import annotations

import MetaTrader5 as mt5


INSTRUMENT_CHOICES = (
    ("XAUUSD", "Gold"),
    ("EURUSD", "Euro / US dollar"),
    ("GBPUSD", "British pound / US dollar"),
    ("USDJPY", "US dollar / Japanese yen"),
    ("AUDUSD", "Australian dollar / US dollar"),
)
TIMEFRAME_CHOICES = ("M15", "H1", "H4")


def resolve_mt5_symbol(preferred: str) -> str:
    """Resolve a common symbol name to the broker's exact MT5 symbol."""

    preferred = preferred.strip()

    if not preferred:
        raise ValueError("The instrument symbol cannot be empty.")

    if mt5.symbol_info(preferred) is not None:
        return preferred

    symbols = mt5.symbols_get()

    if symbols is None:
        raise RuntimeError(
            f"Could not read MetaTrader 5 symbols: {mt5.last_error()}"
        )

    preferred_upper = preferred.upper()
    candidates = [
        item.name
        for item in symbols
        if preferred_upper in item.name.upper()
    ]

    if not candidates:
        raise ValueError(
            f"No MetaTrader 5 symbol matching '{preferred}' was found."
        )

    candidates.sort(
        key=lambda value: (
            not value.upper().startswith(preferred_upper),
            len(value),
            value,
        )
    )
    return candidates[0]


def choose_symbol_interactively() -> str:
    """Ask the user which currency or instrument should be loaded."""

    print("\nCurrency / instrument")
    print("-" * 52)

    for number, (symbol, description) in enumerate(
        INSTRUMENT_CHOICES,
        start=1,
    ):
        default = "  [default]" if number == 1 else ""
        print(f"{number}. {symbol:<8} {description}{default}")

    print(f"{len(INSTRUMENT_CHOICES) + 1}. Custom MT5 symbol")
    print("-" * 52)

    while True:
        answer = input(
            f"Choose an instrument [1-{len(INSTRUMENT_CHOICES) + 1}] "
            "(Enter = XAUUSD): "
        ).strip()

        if not answer:
            return INSTRUMENT_CHOICES[0][0]

        if answer.isdigit():
            selected = int(answer)

            if 1 <= selected <= len(INSTRUMENT_CHOICES):
                return INSTRUMENT_CHOICES[selected - 1][0]

            if selected == len(INSTRUMENT_CHOICES) + 1:
                custom = input("Enter the MT5 symbol: ").strip()

                if custom:
                    return custom

                print("The symbol cannot be empty.")
                continue

        normalized = answer.upper()

        if normalized in {
            symbol for symbol, _ in INSTRUMENT_CHOICES
        }:
            return normalized

        print("Enter a number from the list or a listed symbol.")


def choose_timeframe_interactively() -> str:
    """Ask which candle timeframe should be analyzed or backtested."""

    print("\nTimeframe")
    print("-" * 32)

    for number, timeframe in enumerate(TIMEFRAME_CHOICES, start=1):
        default = "  [default]" if number == 1 else ""
        print(f"{number}. {timeframe}{default}")

    print("-" * 32)

    while True:
        answer = input(
            "Choose a timeframe [1-3] (Enter = M15): "
        ).strip()

        if not answer:
            return "M15"

        normalized = answer.upper()

        if normalized in TIMEFRAME_CHOICES:
            return normalized

        if answer.isdigit():
            selected = int(answer)

            if 1 <= selected <= len(TIMEFRAME_CHOICES):
                return TIMEFRAME_CHOICES[selected - 1]

        print("Enter 1, 2, 3, M15, H1, or H4.")


def select_market(
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> tuple[str, str]:
    """Resolve explicit values or ask for them in the terminal."""

    requested_symbol = symbol or choose_symbol_interactively()
    selected_timeframe = (
        timeframe.upper()
        if timeframe
        else choose_timeframe_interactively()
    )

    if selected_timeframe not in TIMEFRAME_CHOICES:
        raise ValueError("Timeframe must be M15, H1, or H4.")

    if not mt5.initialize():
        raise RuntimeError(
            "Could not connect to MetaTrader 5. "
            f"MetaTrader error: {mt5.last_error()}"
        )

    try:
        resolved_symbol = resolve_mt5_symbol(requested_symbol)
    finally:
        mt5.shutdown()

    print(
        f"\nSelected market: {resolved_symbol} {selected_timeframe}"
    )
    return resolved_symbol, selected_timeframe
