"""Interactive multi-market M15/H1/H4 strategy backtest service."""

from __future__ import annotations

import json
import threading
import webbrowser
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

from .chart import create_interactive_chart, ensure_plotly_js_asset
from .config import OUTPUT_DIRECTORY, PLOTLY_JS_FILE, PROJECT_ROOT
from .market_data import load_market_timeframes
from .indicators import calculate_smc_indicators
from .strategy_backtest import load_strategy_file, run_file_backtest


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8767
DEFAULT_STRATEGY = (
    PROJECT_ROOT / "strategies" / "mtf_trend_pullback.py"
)
EXECUTION_TIMEFRAMES = ("M15", "H1", "H4")
REPORT_FILE = OUTPUT_DIRECTORY / "strategy_backtest.html"
TRADES_FILE = OUTPUT_DIRECTORY / "strategy_trades.csv"
EXECUTION_OVERRIDES = {
    "cash": 500.0,
    "spread": 0.0001,
    "commission": 0.0,
    "margin": 0.002,
    "lot_size": 0.01,
    "contract_size": 100_000.0,
    "leverage": 500.0,
}


def discover_strategy_files() -> list[dict]:
    """Return every valid file-driven strategy under the project folder."""

    strategies = []

    for path in sorted((PROJECT_ROOT / "strategies").rglob("*.py")):
        try:
            module = load_strategy_file(path)
        except Exception:
            continue

        strategies.append(
            {
                "path": path.resolve(),
                "name": str(
                    getattr(module, "STRATEGY_NAME", path.stem)
                ),
                "description": str(
                    getattr(module, "STRATEGY_DESCRIPTION", "")
                ),
                "recommended": path.resolve() == DEFAULT_STRATEGY.resolve(),
            }
        )

    strategies.sort(
        key=lambda item: (
            0 if item["recommended"] else 1,
            item["name"].lower(),
        )
    )
    return strategies


def choose_strategy_interactively() -> Path:
    """Display a numbered strategy list and read one console selection."""

    strategies = discover_strategy_files()

    if not strategies:
        raise RuntimeError("No compatible Python strategy files were found.")

    print("\nAvailable project strategies")
    print("-" * 72)

    for number, item in enumerate(strategies, start=1):
        suffix = (
            "  [recommended]"
            if item["recommended"]
            else ""
        )
        relative = item["path"].relative_to(PROJECT_ROOT)
        print(f"{number:2d}. {item['name']}{suffix}")
        print(f"    {relative}")

    print("-" * 72)

    while True:
        answer = input(
            f"Choose a strategy [1-{len(strategies)}] "
            "(Enter = recommended): "
        ).strip()

        if not answer:
            return strategies[0]["path"]

        try:
            selected = int(answer)
        except ValueError:
            print("Please enter a number from the list.")
            continue

        if 1 <= selected <= len(strategies):
            return strategies[selected - 1]["path"]

        print("That number is outside the available range.")


def _money(value) -> str:
    return "—" if value is None or pd.isna(value) else f"${float(value):,.2f}"


def _percent(value) -> str:
    return "—" if value is None or pd.isna(value) else f"{float(value):,.2f}%"


def _number(value) -> str:
    return "—" if value is None or pd.isna(value) else f"{float(value):,.2f}"


def _trade_rows(trades: pd.DataFrame) -> str:
    if trades.empty:
        return (
            '<tr><td colspan="12">'
            "The selected strategy produced no completed trades."
            "</td></tr>"
        )

    rows = []

    for _, trade in trades.iloc[::-1].iterrows():
        outcome = str(trade["Outcome"])
        css_class = (
            "positive trade-win"
            if outcome == "Win"
            else "negative trade-loss"
            if outcome == "Loss"
            else ""
        )
        rows.append(
            "<tr>"
            f"<td>#{int(trade['Trade'])}</td>"
            f"<td>{escape(str(trade['Direction']))}</td>"
            f"<td>{escape(str(trade['EntryTime']))}</td>"
            f"<td>{float(trade['EntryPrice']):,.5f}</td>"
            f"<td>{float(trade['InitialSL']):,.5f}</td>"
            f"<td>{float(trade['InitialTP']):,.5f}</td>"
            f"<td>{escape(str(trade['ExitTime']))}</td>"
            f"<td>{float(trade['ExitPrice']):,.5f}</td>"
            f'<td class="{css_class}">{float(trade["PnL"]):,.2f}</td>'
            f'<td class="{css_class}">{outcome}</td>'
            f"<td>{escape(str(trade['ExitReason']))}</td>"
            "<td>"
            f"{escape(str(trade['SignalReason']))}"
            "<details><summary>Indicator values</summary>"
            f"<p>{escape(str(trade['IndicatorContext']))}</p>"
            "</details></td>"
            "</tr>"
        )

    return "".join(rows)


def build_trade_history_dashboard(
    *,
    artifacts: dict,
    symbol: str,
    data_source: str,
    execution_timeframe: str,
) -> str:
    """Build the results and audited history below the shared chart."""

    summary = artifacts["summary"]
    settings = artifacts["settings"]
    lot_size = float(settings.get("lot_size", 0.01))
    leverage = float(settings.get("leverage", 500.0))
    net_class = (
        "trade-win"
        if summary["net_pnl"] >= 0
        else "trade-loss"
    )

    return f"""
<section id="chart-summary" class="analysis-dashboard backtest-audit-dashboard">
    <header class="dashboard-header">
        <div>
            <p class="eyebrow">Strategy backtest results</p>
            <h2>{escape(artifacts['strategy_name'])}</h2>
            <p>{escape(artifacts['strategy_description'])}</p>
            <p><strong>{escape(symbol)} {escape(execution_timeframe)}</strong> · {escape(data_source)}</p>
            <p>Strategy file: <code>{escape(artifacts['strategy_path'])}</code></p>
        </div>
        <div class="backtest-links">
            <button class="back-to-chart" type="button" onclick="rerunBacktest(false)">Reload strategy</button>
            <button class="back-to-chart" type="button" onclick="rerunBacktest(true)">Refresh MT5 + rerun</button>
            <a class="back-to-chart" href="trades.csv">Trade CSV</a>
        </div>
    </header>

    <div class="metric-grid backtest-metrics">
        <article class="metric-card metric-primary">
            <span>Win rate</span>
            <strong>{_percent(summary['win_rate_pct'])}</strong>
            <small>{summary['wins']} wins / {summary['losses']} losses</small>
        </article>
        <article class="metric-card">
            <span>Completed trades</span>
            <strong>{summary['trades']}</strong>
            <small>Numbered entries and exits are shown above</small>
        </article>
        <article class="metric-card">
            <span>Starting balance</span>
            <strong>{_money(summary['starting_balance'])}</strong>
            <small>{lot_size:g} lot · {leverage:g}:1 leverage</small>
        </article>
        <article class="metric-card">
            <span>Final balance</span>
            <strong>{_money(summary['final_balance'])}</strong>
            <small>Return {_percent(summary['return_pct'])}</small>
        </article>
        <article class="metric-card">
            <span>Net P&amp;L</span>
            <strong class="{net_class}">{_money(summary['net_pnl'])}</strong>
            <small>Profit factor {_number(summary['profit_factor'])}</small>
        </article>
        <article class="metric-card">
            <span>Maximum drawdown</span>
            <strong class="trade-loss">{_percent(summary['max_drawdown_pct'])}</strong>
            <small>Historical simulation</small>
        </article>
    </div>

    <div class="trade-table-wrap">
        <table class="trade-table">
            <caption>Complete executed trade history and strategy audit</caption>
            <thead>
                <tr>
                    <th>#</th><th>Side</th><th>Entry time</th><th>Entry</th>
                    <th>SL</th><th>TP</th><th>Exit time</th><th>Exit</th>
                    <th>P&amp;L</th><th>Outcome</th><th>Exit reason</th>
                    <th>Entry rule and indicator values</th>
                </tr>
            </thead>
            <tbody>{_trade_rows(artifacts['trades'])}</tbody>
        </table>
    </div>

    <p class="analysis-disclaimer">
        Hypothetical historical simulation only—not a forecast or trading recommendation.
        Hover matching numbered markers on the chart to verify each table row visually.
    </p>
</section>
<script>
async function rerunBacktest(refresh) {{
    const original = await fetch("/api/status").then(item => item.json());
    const response = await fetch(
        `/api/rerun?refresh=${{refresh ? 1 : 0}}`,
        {{method: "POST"}}
    );

    if (!response.ok) {{
        alert(await response.text());
        return;
    }}

    const poll = window.setInterval(async () => {{
        const state = await fetch("/api/status").then(item => item.json());

        if (state.error) {{
            window.clearInterval(poll);
            alert(state.error);
        }} else if (!state.running && state.version > original.version) {{
            window.clearInterval(poll);
            location.reload();
        }}
    }}, 1000);
}}
</script>
"""


def write_report(
    *,
    frames: dict[str, pd.DataFrame],
    artifacts: dict,
    symbol: str,
    data_source: str,
    execution_timeframe: str = "M15",
) -> None:
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    artifacts["trades"].to_csv(TRADES_FILE, index=False)
    price_columns = [
        column
        for column in (
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume",
        )
        if column in frames[execution_timeframe].columns
    ]
    price_history = frames[execution_timeframe][price_columns].copy()
    dashboard_html = build_trade_history_dashboard(
        artifacts=artifacts,
        symbol=symbol,
        data_source=data_source,
        execution_timeframe=execution_timeframe,
    )
    create_interactive_chart(
        results=price_history,
        number_of_candles=len(price_history),
        output_file=REPORT_FILE,
        trade_overlays=artifacts["trades"],
        custom_dashboard_html=dashboard_html,
        include_analysis_dashboard=False,
        trade_history_mode=True,
        defer_plotly_loading=False,
    )


class MarketBacktestController:
    def __init__(
        self,
        strategy_path: Path,
        preferred_symbol: str,
        execution_timeframe: str = "M15",
    ):
        execution_timeframe = execution_timeframe.upper()

        if execution_timeframe not in EXECUTION_TIMEFRAMES:
            raise ValueError(
                "Execution timeframe must be M15, H1, or H4."
            )

        self.strategy_path = strategy_path.resolve()
        self.preferred_symbol = preferred_symbol
        self.execution_timeframe = execution_timeframe
        self.frames: dict[str, pd.DataFrame] | None = None
        self.symbol = preferred_symbol
        self.contract_size = 100.0
        self.data_source = ""
        self._run_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._state = {
            "version": 0,
            "running": False,
            "error": None,
            "message": "Waiting to run",
            "strategy_path": str(self.strategy_path),
            "strategy_name": self.strategy_path.stem,
            "symbol": self.symbol,
            "execution_timeframe": self.execution_timeframe,
        }

    def status(self) -> dict:
        with self._state_lock:
            return dict(self._state)

    def _update(self, **values) -> None:
        with self._state_lock:
            self._state.update(values)
            self._state["updated_at"] = pd.Timestamp.now(
                tz="UTC"
            ).isoformat()

    def generate(self, *, refresh_data: bool) -> None:
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("A strategy backtest is already running.")

        self._update(
            running=True,
            error=None,
            message=(
                "Downloading M15/H1/H4 from MetaTrader 5"
                if refresh_data
                else "Reloading strategy and cached candles"
            ),
        )

        try:
            if refresh_data or self.frames is None:
                (
                    self.frames,
                    self.data_source,
                    self.symbol,
                    self.contract_size,
                ) = load_market_timeframes(
                    refresh_data=refresh_data,
                    preferred_symbol=self.preferred_symbol,
                )

            self._update(
                message=(
                    f"Preparing {self.execution_timeframe} indicators"
                )
            )
            execution_data = self.frames[self.execution_timeframe]

            if "Trend_State" not in execution_data.columns:
                execution_data = calculate_smc_indicators(execution_data)
                self.frames[self.execution_timeframe] = execution_data

            self._update(
                message=(
                    f"Executing selected strategy on "
                    f"{self.execution_timeframe}"
                )
            )
            artifacts = run_file_backtest(
                execution_data,
                self.strategy_path,
                setting_overrides={
                    **EXECUTION_OVERRIDES,
                    "contract_size": self.contract_size,
                },
            )
            self._update(message="Building M15/H1/H4 audit report")
            write_report(
                frames=self.frames,
                artifacts=artifacts,
                symbol=self.symbol,
                data_source=self.data_source,
                execution_timeframe=self.execution_timeframe,
            )
            summary = artifacts["summary"]

            with self._state_lock:
                self._state.update(
                    {
                        "version": self._state["version"] + 1,
                        "running": False,
                        "error": None,
                        "message": "Strategy backtest ready",
                        "strategy_name": artifacts["strategy_name"],
                        "symbol": self.symbol,
                        "execution_timeframe": self.execution_timeframe,
                        "win_rate_pct": summary["win_rate_pct"],
                        "trades": summary["trades"],
                        "net_pnl": summary["net_pnl"],
                        "last_candle_time": str(
                            self.frames[self.execution_timeframe][
                                "time"
                            ].iloc[-1]
                        ),
                        "updated_at": pd.Timestamp.now(
                            tz="UTC"
                        ).isoformat(),
                    }
                )
        except Exception as error:
            self._update(
                running=False,
                error=str(error),
                message="Strategy backtest failed",
            )
            raise
        finally:
            self._run_lock.release()

    def start_async(self, *, refresh_data: bool) -> bool:
        if self.status()["running"]:
            return False

        def worker() -> None:
            try:
                self.generate(refresh_data=refresh_data)
            except Exception as error:
                print(f"\nStrategy backtest warning: {error}")

        threading.Thread(
            target=worker,
            name="market-backtest-runner",
            daemon=True,
        ).start()
        return True


def build_request_handler(controller: MarketBacktestController):
    class Handler(BaseHTTPRequestHandler):
        def send_bytes(
            self,
            status: int,
            content_type: str,
            payload: bytes,
            *,
            cache_control: str = "no-store",
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", cache_control)
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            path = urlparse(self.path).path

            if path == "/api/status":
                self.send_bytes(
                    200,
                    "application/json; charset=utf-8",
                    json.dumps(controller.status()).encode("utf-8"),
                )
                return

            if path in {"/", "/backtest", f"/{REPORT_FILE.name}"}:
                if not REPORT_FILE.exists():
                    self.send_bytes(
                        503,
                        "text/plain; charset=utf-8",
                        b"The backtest report is still being generated.",
                    )
                    return

                self.send_bytes(
                    200,
                    "text/html; charset=utf-8",
                    REPORT_FILE.read_bytes(),
                )
                return

            if path in {"/trades.csv", f"/{TRADES_FILE.name}"}:
                self.send_bytes(
                    200,
                    "text/csv; charset=utf-8",
                    TRADES_FILE.read_bytes(),
                )
                return

            if path == f"/{PLOTLY_JS_FILE}":
                plotly_path = ensure_plotly_js_asset()
                self.send_bytes(
                    200,
                    "application/javascript; charset=utf-8",
                    plotly_path.read_bytes(),
                    cache_control="public, max-age=86400",
                )
                return

            if path == "/favicon.ico":
                self.send_bytes(204, "image/x-icon", b"")
                return

            self.send_bytes(
                404,
                "text/plain; charset=utf-8",
                b"Not found",
            )

        def do_POST(self) -> None:
            parsed = urlparse(self.path)

            if parsed.path != "/api/rerun":
                self.send_bytes(
                    404,
                    "text/plain; charset=utf-8",
                    b"Not found",
                )
                return

            refresh = parse_qs(parsed.query).get("refresh", ["0"])[0]

            if not controller.start_async(
                refresh_data=refresh in {"1", "true", "yes"}
            ):
                self.send_bytes(
                    409,
                    "text/plain; charset=utf-8",
                    b"A strategy backtest is already running.",
                )
                return

            self.send_bytes(
                202,
                "application/json; charset=utf-8",
                b'{"accepted": true}',
            )

        def log_message(self, format_string: str, *args) -> None:
            pass

    return Handler


def start_server(
    controller: MarketBacktestController,
    *,
    host: str,
    port: int,
) -> tuple[ThreadingHTTPServer, str]:
    handler = build_request_handler(controller)
    last_error = None

    for candidate in range(port, port + 10):
        try:
            server = ThreadingHTTPServer((host, candidate), handler)
            server.daemon_threads = True
            return server, f"http://{host}:{candidate}/"
        except OSError as error:
            last_error = error

    raise RuntimeError(
        f"Could not start the backtest service near port {port}: {last_error}"
    )


def run_selected_backtest(
    *,
    symbol: str,
    timeframe: str,
    strategy_path: Path | None = None,
    use_cache: bool = False,
    no_browser: bool = False,
    once: bool = False,
) -> None:
    """Run one selected market and serve its strategy audit report."""

    selected_strategy = (
        strategy_path
        if strategy_path is not None
        else choose_strategy_interactively()
    )
    controller = MarketBacktestController(
        selected_strategy,
        symbol,
        timeframe,
    )

    print("\n" + "=" * 64)
    print(" MULTI-MARKET STRATEGY BACKTEST")
    print("=" * 64)
    print(f"Selected strategy: {controller.strategy_path}")
    print(f"Symbol: {symbol}")
    print(f"Execution timeframe: {controller.execution_timeframe}")
    print("\nDownloading candles and building the audit report...")
    controller.generate(refresh_data=not use_cache)
    state = controller.status()

    print("\n" + "=" * 64)
    print(" BACKTEST READY")
    print("=" * 64)
    print(f"Resolved symbol: {state['symbol']}")
    print(f"Timeframe:       {state['execution_timeframe']}")
    print(f"Trades:          {state['trades']}")
    print(f"Win rate:        {_percent(state['win_rate_pct'])}")
    print(f"Net P&L:         {_money(state['net_pnl'])}")
    print(f"Report:          {REPORT_FILE.resolve()}")

    if once:
        return

    server, url = start_server(
        controller,
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
    )
    print(f"\nBacktest URL: {url}")
    print("Press Ctrl+C to stop the service.")

    if not no_browser:
        webbrowser.open_new_tab(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStrategy backtest service stopped.")
    finally:
        server.shutdown()
        server.server_close()
