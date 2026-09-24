#!/bin/bash
# Runs inside the vllm/vllm-openai container on the GPU instance. Exercises everything Ookami does today.
set -uo pipefail
step() { echo; echo "=== $* ($(date +%H:%M:%S))"; }
fail=0; check() { if "$@"; then echo "OK: $*"; else echo "FAILED($?): $*"; fail=1; fi; }

step install
python3 -m pip install -q "$(ls /wheel/*.whl)[gateway,train-cuda]" || exit 1
mkdir -p /work && cp /kit/* /work/ && cd /work
python3 make_data.py > tickets.jsonl
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
python3 -c "import torch, vllm, trl, peft; print('torch', torch.__version__, 'bf16', torch.cuda.is_bf16_supported(), 'vllm', vllm.__version__, 'trl', trl.__version__, 'peft', peft.__version__)"

step validate
check ookami validate -f ookami.yaml

step "serve the base model with vLLM behind the gateway"
check ookami up -f ookami.yaml --timeout 900
export OOKAMI_API_KEY=$(ookami keys master -f ookami.yaml)
check ookami status -f ookami.yaml
check ookami down -f ookami.yaml

step "train (TRL LoRA) -> gate vs base (vLLM multi-LoRA)"
ookami train router -f ookami.yaml; echo "train exit $?"
ookami jobs -f ookami.yaml
ookami models -f ookami.yaml
cat .ookami/reports/router/*.md 2>/dev/null

step "promote and serve the trained version"
check ookami promote router -f ookami.yaml
check ookami up -f ookami.yaml --timeout 900
export OOKAMI_API_KEY=$(ookami keys master -f ookami.yaml)
python3 - <<'PY' || fail=1
import json, os, urllib.request
cases = {"Hi, I was charged twice for my kettle": "BILL-7", "The courier left my tent at the wrong address": "SHIP-2",
         "Please refund the yoga mat I returned": "REF-9", "Can I get a gift wrap for the monitor": "GEN-1"}
right = 0
for t, want in cases.items():
    body = {"model": "router", "max_tokens": 16, "temperature": 0, "messages": [{"role": "user", "content":
            f"Route this support ticket to a queue. Reply with the queue code only.\n\nTicket: {t}"}]}
    req = urllib.request.Request("http://127.0.0.1:4000/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["OOKAMI_API_KEY"]})
    got = json.load(urllib.request.urlopen(req, timeout=120))["choices"][0]["message"]["content"].strip()
    right += got == want
    print(f"{t!r} -> {got} (want {want})")
print(f"gateway: {right}/{len(cases)} routed correctly")
PY
check ookami down -f ookami.yaml

step done
[ $fail = 0 ] && echo "AWS-GPU-TEST: PASS" || echo "AWS-GPU-TEST: SOME CHECKS FAILED"
