#!/usr/bin/env python3
"""Dependency-free HTTP server for the StormShift demo."""

import argparse
import json
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from stormshift.agents import negotiate
from stormshift.scenario import get_scenario

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"


class StormShiftHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def _json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/health":
            return self._json({"status": "ok", "service": "stormshift"})
        if self.path == "/api/scenario":
            return self._json(get_scenario())
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/negotiate":
            return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            return self._json(negotiate(get_scenario(), payload.get("preference", "balanced")))
        except (ValueError, KeyError, TypeError) as exc:
            return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, fmt, *args):
        print(f"[stormshift] {self.address_string()} {fmt % args}")


def main():
    parser = argparse.ArgumentParser(description="Run StormShift")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), StormShiftHandler)
    print(f"StormShift running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
