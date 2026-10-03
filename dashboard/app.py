"""Run the API-driven local disaster operations dashboard."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import re
from urllib.parse import parse_qs, unquote, urlparse

from dashboard.config import DashboardConfig
from dashboard.components import render_dashboard
from dashboard.api_client import DashboardAPIClient, DashboardAPIError
from dashboard.live_data import load_live_snapshot
from dashboard.live_view import render_live_dashboard
from pathlib import Path


def make_handler(dataset):
    """Legacy explicit-data handler retained for Phase 10 compatibility tests.

    The application entry point does not call this handler or load artifacts.
    """
    page = render_dashboard(dataset).encode("utf-8")
    health = json.dumps({"status": "ok", "phase": 10}).encode("utf-8")

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if urlparse(self.path).path == "/healthz":
                body, content_type = health, "application/json; charset=utf-8"
            elif urlparse(self.path).path == "/":
                body, content_type = page, "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            print("dashboard: " + fmt % args)

    return DashboardHandler


def make_live_handler(config=None, *, client=None):
    """Serve a static interface whose operational data comes only from Phase 11 API."""
    config = config or DashboardConfig.from_env()
    api = client or DashboardAPIClient(config.api_base_url, config.api_timeout_seconds)
    page = render_live_dashboard().encode("utf-8")
    world_map = (Path(__file__).with_name("world_countries.geojson")).read_bytes()
    detail_route = re.compile(r"^/dashboard-event/([^/]+)$")

    class LiveDashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._write(200, page, "text/html; charset=utf-8")
                return
            if parsed.path == "/world-countries.geojson":
                self._write(200, world_map, "application/geo+json; charset=utf-8")
                return
            if parsed.path == "/healthz":
                try:
                    health = api.get_json("/health")
                    okay = health.get("status") == "ok" and health.get("database") == "ok"
                except DashboardAPIError:
                    health, okay = {"status": "offline", "database": "unknown"}, False
                self._write(200 if okay else 503,
                    json.dumps({"status": "ok" if okay else "degraded", "phase": "11.5",
                                "api": health.get("status"), "database": health.get("database")},
                               separators=(",", ":")).encode("utf-8"), "application/json; charset=utf-8")
                return
            if parsed.path == "/dashboard-data":
                query = {key: values[-1] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}
                query = {key: value[:500] for key, value in query.items() if key in {
                    "source", "disaster_type", "operational_type", "severity", "priority", "location",
                    "alert_level", "since", "until", "offset"}}
                snapshot = load_live_snapshot(api, query)
                self._json(200, snapshot)
                return
            if match := detail_route.match(parsed.path):
                try:
                    item = api.get_json("/api/events/" + unquote(match.group(1)))
                    self._json(200, item)
                except DashboardAPIError as exc:
                    status = exc.status if exc.status in {404} else (503 if exc.status is None else 502)
                    self._json(status, {"error": {"message": str(exc)}})
                return
            self._json(404, {"error": {"message": "route not found"}})

        def _json(self, status, value):
            self._write(status, json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                allow_nan=False).encode("utf-8"), "application/json; charset=utf-8")

        def _write(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            logging.getLogger("dashboard.http").info(fmt, *args)

    return LiveDashboardHandler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=None, help="bind address (default: DASHBOARD_HOST or loopback)")
    parser.add_argument("--port", type=int, default=None, help="dashboard port (default: DASHBOARD_PORT or 8765)")
    parser.add_argument("--api-url", default=None, help="Phase 11 API base URL (default: DASHBOARD_API_BASE_URL)")
    parser.add_argument("--api-timeout", type=float, default=None, help="API timeout in seconds")
    args = parser.parse_args(argv)
    configured = DashboardConfig.from_env()
    config = DashboardConfig(host=args.host or configured.host,
        port=args.port if args.port is not None else configured.port,
        api_base_url=args.api_url or configured.api_base_url,
        api_timeout_seconds=args.api_timeout if args.api_timeout is not None else configured.api_timeout_seconds)
    server = ThreadingHTTPServer((config.host, config.port), make_live_handler(config))
    print(f"Phase 11.5 dashboard: http://{config.host}:{server.server_port}/")
    print(f"Operational API configured: {urlparse(config.api_base_url).hostname or 'local'}")
    print("Dashboard data is read from the API. API outages and empty databases do not load demo records.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
