#!/bin/bash
# Installs forge from the mounted wheel, brings up model + gateway, checks a chat call and the eval gate.
set -euo pipefail
PIP="python3 -m pip install -q"
$PIP --break-system-packages "$(ls /wheel/*.whl)[gateway]" 2>/dev/null || $PIP "$(ls /wheel/*.whl)[gateway]"
mkdir -p /work && cp /config/* /work/ && cd /work
forge validate -f forge.yaml
forge up -f forge.yaml --timeout 900
forge status -f forge.yaml
python3 - <<'PY'
import json, urllib.request
body = {"model": "qwen-05b", "messages": [{"role": "user", "content": "What is the capital of France? One word."}],
        "max_tokens": 16, "temperature": 0}
req = urllib.request.Request("http://127.0.0.1:4000/v1/chat/completions", json.dumps(body).encode(),
                             {"Content-Type": "application/json"})
print("GATEWAY REPLY:", json.load(urllib.request.urlopen(req, timeout=120))["choices"][0]["message"]["content"])
PY
forge eval -f bench.yaml --candidate "openai:http://127.0.0.1:4000/v1#qwen-05b" \
  --incumbent "openai:http://127.0.0.1:8100/v1#qwen-05b" && echo "GATE: pass" || echo "GATE: exit $?"
echo SMOKE-DONE
sleep infinity
