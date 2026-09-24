"""A toy benchmark showing the `command` evaluator contract: call the model, write one JSONL line per item."""
import argparse
import json
import urllib.request

ITEMS = [("capital-fr", "What is the capital of France? One word.", "paris"),
         ("two-plus-two", "What is 2+2? Digits only.", "4")]


def ask(base_url: str, model: str, prompt: str) -> str:
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0}
    req = urllib.request.Request(f"{base_url}/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"] or ""


p = argparse.ArgumentParser()
p.add_argument("--base-url", required=True)
p.add_argument("--model", required=True)
p.add_argument("--out", required=True)
a = p.parse_args()
with open(a.out, "w") as f:
    for item_id, prompt, want in ITEMS:
        try:
            got = ask(a.base_url, a.model, prompt).strip().lower()
            ok = want in got
            f.write(json.dumps({"item_id": item_id, "score": float(ok), "passed": ok}) + "\n")
        except OSError as e:
            f.write(json.dumps({"item_id": item_id, "error": True, "reason": str(e)}) + "\n")
