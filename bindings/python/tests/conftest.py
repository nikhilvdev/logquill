"""Shared fixtures."""

import http.server
import threading

import pytest


class Recorder:
    """A local HTTP server that remembers every POST it gets and answers with `status`."""

    def __init__(self):
        self.requests = []
        self.status = 200
        recorder = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                recorder.requests.append(
                    {"path": self.path, "headers": dict(self.headers.items()), "body": body}
                )
                self.send_response(recorder.status)
                self.end_headers()

            do_PUT = do_POST

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def bodies(self):
        return [request["body"].decode() for request in self.requests]


@pytest.fixture
def recorder():
    recorder = Recorder()
    yield recorder
    recorder.server.shutdown()
