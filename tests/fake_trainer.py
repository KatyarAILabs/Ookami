"""A stand-in trainer: writes an 'adapter' whose served answer is $FAKE_ANSWER. $FAKE_TRAIN_FAIL makes it fail."""
import json
import os
import sys
from pathlib import Path

job = json.loads(Path(sys.argv[1]).read_text())
if os.environ.get("FAKE_TRAIN_FAIL"):
    print("simulated trainer crash")
    sys.exit(2)
out = Path(job["adapter_dir"])
out.mkdir(parents=True, exist_ok=True)
(out / "adapters.safetensors").write_bytes(b"fake")
(out / "answer.txt").write_text(os.environ.get("FAKE_ANSWER", "ok"))
(out / "seen_plan.json").write_text(json.dumps(job["plan"]))
print("trained", job["plan"]["iters"], "iters on", job["dataset_dir"])
