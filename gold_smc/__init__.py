"""MetaTrader 5 Smart Money Concepts analysis and backtesting."""

import sys


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

__all__: list[str] = []
