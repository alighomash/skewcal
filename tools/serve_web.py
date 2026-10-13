#!/usr/bin/env python3
"""Serve web/ locally: python3 tools/serve_web.py [port]   then open http://localhost:8765

index.html is written as an Artifact page body (no doctype/head); this server adds
the same minimal skeleton the Artifact host adds, so the page renders identically.
"""
import http.server
import os
import sys

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_web import wrapped_index  # noqa: E402


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".wasm": "application/wasm", ".whl": "application/zip", ".js": "text/javascript"}

    def __init__(self, *a, **k):
        super().__init__(*a, directory=WEB, **k)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = wrapped_index().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"Skewcal at http://localhost:{port}")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
