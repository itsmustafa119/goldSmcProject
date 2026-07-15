import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from .chart import build_mplfinance_viewer
from .config import (
    BACKTEST_OUTPUT_FILE,
    BACKTEST_TRADES_FILE,
    HTML_OUTPUT_FILE,
    LIVE_HOST,
    LIVE_PORT,
    LIVE_REFRESH_SECONDS,
    MPLFINANCE_OUTPUT_FILE,
    PLOTLY_JS_FILE,
    project_path,
)

LIVE_STATE = {
    "version": 0,
    "last_candle_time": None,
    "last_price": None,
    "updated_at": None,
    "error": None,
}
LIVE_STATE_LOCK = threading.Lock()


def update_live_state(
    *,
    last_candle_time=None,
    last_price=None,
    error=None,
    increment_version: bool = False,
) -> None:
    with LIVE_STATE_LOCK:
        if increment_version:
            LIVE_STATE["version"] += 1

        if last_candle_time is not None:
            LIVE_STATE["last_candle_time"] = str(
                last_candle_time
            )

        if last_price is not None:
            LIVE_STATE["last_price"] = float(
                last_price
            )

        LIVE_STATE["updated_at"] = (
            pd.Timestamp.now(tz="UTC").isoformat()
        )
        LIVE_STATE["error"] = error


class LiveDashboardRequestHandler(BaseHTTPRequestHandler):
    def send_bytes(
        self,
        status_code: int,
        content_type: str,
        payload: bytes,
        cache_control: str = "no-store, no-cache, must-revalidate",
    ) -> None:
        self.send_response(status_code)
        self.send_header(
            "Content-Type",
            content_type,
        )
        self.send_header(
            "Content-Length",
            str(len(payload)),
        )
        self.send_header(
            "Cache-Control",
            cache_control,
        )
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        request_path = urlparse(
            self.path
        ).path

        if request_path == "/api/status":
            with LIVE_STATE_LOCK:
                payload = json.dumps(
                    LIVE_STATE
                ).encode("utf-8")

            self.send_bytes(
                200,
                "application/json; charset=utf-8",
                payload,
            )
            return

        if request_path in {"/", "/chart"}:
            chart_path = project_path(
                HTML_OUTPUT_FILE
            ).resolve()

            if not chart_path.exists():
                self.send_bytes(
                    503,
                    "text/plain; charset=utf-8",
                    b"Chart is still being generated.",
                )
                return

            self.send_bytes(
                200,
                "text/html; charset=utf-8",
                chart_path.read_bytes(),
            )
            return

        if request_path == f"/{PLOTLY_JS_FILE}":
            plotly_path = project_path(
                PLOTLY_JS_FILE
            ).resolve()

            if not plotly_path.exists():
                self.send_bytes(
                    503,
                    "text/plain; charset=utf-8",
                    b"The local Plotly runtime is still being generated.",
                )
                return

            self.send_bytes(
                200,
                "application/javascript; charset=utf-8",
                plotly_path.read_bytes(),
                cache_control="public, max-age=86400",
            )
            return

        if request_path == "/mplfinance":
            self.send_bytes(
                200,
                "text/html; charset=utf-8",
                build_mplfinance_viewer().encode("utf-8"),
            )
            return

        if request_path in {
            "/mplfinance.png",
            f"/{MPLFINANCE_OUTPUT_FILE}",
        }:
            snapshot_path = project_path(
                MPLFINANCE_OUTPUT_FILE
            ).resolve()

            if not snapshot_path.exists():
                self.send_bytes(
                    503,
                    "text/plain; charset=utf-8",
                    b"The mplfinance chart is still being generated.",
                )
                return

            self.send_bytes(
                200,
                "image/png",
                snapshot_path.read_bytes(),
            )
            return

        if request_path in {
            "/backtest",
            f"/{BACKTEST_OUTPUT_FILE}",
        }:
            backtest_path = project_path(
                BACKTEST_OUTPUT_FILE
            ).resolve()

            if not backtest_path.exists():
                self.send_bytes(
                    503,
                    "text/plain; charset=utf-8",
                    b"The backtest is still being generated.",
                )
                return

            self.send_bytes(
                200,
                "text/html; charset=utf-8",
                backtest_path.read_bytes(),
            )
            return

        if request_path in {
            "/backtest-trades.csv",
            f"/{BACKTEST_TRADES_FILE}",
        }:
            trades_path = project_path(
                BACKTEST_TRADES_FILE
            ).resolve()

            if not trades_path.exists():
                self.send_bytes(
                    503,
                    "text/plain; charset=utf-8",
                    b"The backtest trade list is still being generated.",
                )
                return

            self.send_bytes(
                200,
                "text/csv; charset=utf-8",
                trades_path.read_bytes(),
            )
            return

        if request_path == "/favicon.ico":
            self.send_bytes(
                204,
                "image/x-icon",
                b"",
            )
            return

        self.send_bytes(
            404,
            "text/plain; charset=utf-8",
            b"Not found",
        )

    def log_message(
        self,
        format_string: str,
        *args,
    ) -> None:
        pass


def start_live_dashboard_server(
) -> tuple[ThreadingHTTPServer, str]:
    last_error = None

    for port in range(
        LIVE_PORT,
        LIVE_PORT + 10,
    ):
        try:
            server = ThreadingHTTPServer(
                (LIVE_HOST, port),
                LiveDashboardRequestHandler,
            )
            server.daemon_threads = True

            thread = threading.Thread(
                target=server.serve_forever,
                name="smc-live-dashboard",
                daemon=True,
            )
            thread.start()

            return (
                server,
                f"http://{LIVE_HOST}:{port}/",
            )
        except OSError as error:
            last_error = error

    raise RuntimeError(
        "Could not start the local dashboard server on "
        f"ports {LIVE_PORT}-{LIVE_PORT + 9}: {last_error}"
    )
