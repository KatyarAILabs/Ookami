#!/bin/bash
# Train -> gate -> promote -> serve on the GPU, then one call through the gateway.
set -euo pipefail
python3 -m pip install -q "$(ls /wheel/*.whl)[gateway,train-cuda]"
mkdir -p /work && cp /config/* /work/ && cd /work
python3 make_data.py > tickets.jsonl
nvidia-smi --query-gpu=name,memory.total --format=csv
ookami validate -f ookami.yaml
ookami train router -f ookami.yaml || { echo "TRAIN: exit $?"; ookami jobs -f ookami.yaml; }
ookami models -f ookami.yaml
ookami promote router -f ookami.yaml
ookami up -f ookami.yaml --timeout 900
export OOKAMI_API_KEY=$(ookami keys master -f ookami.yaml)
python3 - <<'PY'
import json, os, urllib.request
for t in ["Hi, I was charged twice for my kettle", "The courier left my tent at the wrong address"]:
    body = {"model": "router", "max_tokens": 16, "temperature": 0, "messages": [{"role": "user", "content":
            f"Route this support ticket to a queue. Reply with the queue code only.\n\nTicket: {t}"}]}
    req = urllib.request.Request("http://127.0.0.1:4000/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["OOKAMI_API_KEY"]})
    print(t, "->", json.load(urllib.request.urlopen(req, timeout=120))["choices"][0]["message"]["content"])
PY
echo GPU-TEST-DONE
sleep infinity
