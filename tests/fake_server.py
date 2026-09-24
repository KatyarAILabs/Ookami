"""A stand-in OpenAI-compatible server for tests.

argv: port [name] [adapter_dir]. Answers "ok" from the base model; with an adapter, answers whatever the
fake trainer wrote to adapter_dir/answer.txt, so a "trained" version can be made better or worse.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

NAME = sys.argv[2] if len(sys.argv) > 2 else "fake"
ANSWER = (Path(sys.argv[3]) / "answer.txt").read_text() if len(sys.argv) > 3 else "ok"


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self._send({"object": "list", "data": [{"id": NAME}]})

    def do_POST(self):
        self._send({"choices": [{"message": {"role": "assistant", "content": ANSWER}}]})

    def _send(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
