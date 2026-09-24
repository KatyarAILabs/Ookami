"""Your definition of a good answer for the gate. Replace with a real check for your task."""
from ookami import Score, evaluator


@evaluator(name="check", version="1")
def score(example, output) -> Score:
    want = (example.meta.get("reference") or {}).get("content", "").strip().lower()
    got = (output.get("content") if isinstance(output, dict) else str(output) or "").strip().lower()
    ok = bool(want) and got == want
    return Score(float(ok), passed=ok)
