#!/bin/bash
# Train -> gate -> promote -> serve on the GPU, then one call through the gateway.
set -euo pipefail
python3 -m pip install -q "$(ls /wheel/*.whl)[gateway,train-cuda]"
mkdir -p /work && cp /config/* /work/ && cd /work
python3 make_data.py > tickets.jsonl
nvidia-smi --query-gpu=name,memory.total --format=csv
forge validate -f forge.yaml
forge train router -f forge.yaml || { echo "TRAIN: exit $?"; forge jobs -f forge.yaml; }
forge models -f forge.yaml
forge promote router -f forge.yaml
forge up -f forge.yaml --timeout 900
python3 - <<'PY'
import json, urllib.request
for t in ["Hi, I was charged twice for my kettle", "The courier left my tent at the wrong address"]:
    body = {"model": "router", "max_tokens": 16, "temperature": 0, "messages": [{"role": "user", "content":
            f"Route this support ticket to a queue. Reply with the queue code only.\n\nTicket: {t}"}]}
    req = urllib.request.Request("http://127.0.0.1:4000/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    print(t, "->", json.load(urllib.request.urlopen(req, timeout=120))["choices"][0]["message"]["content"])
PY
echo GPU-TEST-DONE
sleep infinity
