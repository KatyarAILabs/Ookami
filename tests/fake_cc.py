#!/usr/bin/env python3
"""A stand-in for Trajectory's `cc` CLI.

cc run -config <file>      serves /healthz on the config's `health_port`
cc export -lake <dir> ... -out <file>   copies <lake>/chat.jsonl to <file> and records its argv in <lake>/argv.json
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

cmd, args = sys.argv[1], sys.argv[2:]
if cmd == "run":
    conf = dict(line.split(": ", 1) for line in Path(args[args.index("-config") + 1]).read_text().splitlines() if ": " in line)

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    HTTPServer(("127.0.0.1", int(conf["health_port"])), H).serve_forever()
elif cmd == "export":
    lake = Path(args[args.index("-lake") + 1])
    (lake / "argv.json").write_text(json.dumps(args))
    Path(args[args.index("-out") + 1]).write_text((lake / "chat.jsonl").read_text())
else:
    sys.exit(f"fake cc: unsupported {cmd}")
