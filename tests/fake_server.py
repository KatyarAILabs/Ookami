"""A stand-in OpenAI-compatible server for tests: answers every GET with 200 and chat calls with 'ok'."""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self._send({"object": "list", "data": [{"id": sys.argv[2] if len(sys.argv) > 2 else "fake"}]})

    def do_POST(self):
        self._send({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

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
