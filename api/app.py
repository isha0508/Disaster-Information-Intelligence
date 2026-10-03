"""Dependency-free WSGI REST API over the Phase 11 SQLite repositories."""

import json
import logging
import re
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from api.config import APIConfig
from api.dependencies import get_repository
from api.routes import alerts, events, intelligence, monitoring
from api.schemas import APIError, response
from database.serialization import sanitize_json


_LOG = logging.getLogger("disaster.api")
_EVENT_DETAIL = re.compile(r"^/api/events/([^/]+)$")
_ALERT_DETAIL = re.compile(r"^/api/alerts/([^/]+)$")
_ALERT_HISTORY = re.compile(r"^/api/alerts/([^/]+)/history$")
_RUN_DETAIL = re.compile(r"^/api/monitoring/runs/([^/]+)$")


def create_app(repository=None, *, db_path=None):
    """Create a WSGI app; callers may inject a temporary repository in tests."""
    repo = repository or get_repository(db_path)

    def application(environ, start_response):
        try:
            if environ.get("REQUEST_METHOD", "GET").upper() != "GET":
                raise APIError(405, "method_not_allowed", "only GET is supported")
            path = environ.get("PATH_INFO", "/")
            params = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
            if path in {"/health", "/healthz"}:
                try:
                    healthy = repo.health_check()
                except Exception:
                    healthy = False
                if not healthy:
                    raise APIError(503, "database_unavailable", "database connectivity check failed")
                body, status = response(status="ok", phase="11.2", database="ok"), 200
            elif path == "/api/events":
                body, status = events.list_events(repo, params), 200
            elif path == "/api/alerts":
                body, status = alerts.list_alerts(repo, params), 200
            elif path == "/api/monitoring/runs":
                body, status = monitoring.list_runs(repo, params), 200
            elif path == "/api/intelligence/summary":
                body, status = intelligence.summary(repo), 200
            elif match := _EVENT_DETAIL.match(path):
                body, status = events.event_detail(repo, _unquote(match.group(1))), 200
            elif match := _ALERT_HISTORY.match(path):
                body, status = alerts.alert_history(repo, _unquote(match.group(1))), 200
            elif match := _ALERT_DETAIL.match(path):
                body, status = alerts.alert_detail(repo, _unquote(match.group(1))), 200
            elif match := _RUN_DETAIL.match(path):
                body, status = monitoring.run_detail(repo, _unquote(match.group(1))), 200
            else:
                raise APIError(404, "not_found", "route not found")
        except APIError as exc:
            body, status = {"error": {"code": exc.code, "message": exc.message}}, exc.status
        except Exception:
            _LOG.exception("API request failed")
            body, status = {"error": {"code": "internal_error", "message": "request could not be completed"}}, 500
        encoded = json.dumps(sanitize_json(body), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        phrase = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed", 500: "Internal Server Error", 503: "Service Unavailable"}.get(status, "Error")
        start_response(f"{status} {phrase}", [("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(encoded))), ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")])
        return [encoded]

    application.repository = repo
    return application


def _unquote(value):
    from urllib.parse import unquote
    return unquote(value)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--db-path", default=None)
    args = parser.parse_args(argv)
    env = APIConfig.from_env()
    host, port = args.host or env.host, args.port if args.port is not None else env.port
    if not 0 <= port <= 65535:
        parser.error("--port must be within 0..65535")
    app = create_app(db_path=args.db_path)
    server = make_server(host, port, app)
    print(f"Phase 11.2 API listening at http://{host}:{server.server_port}")
    print("Local development only: API authentication is not configured.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("API stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
