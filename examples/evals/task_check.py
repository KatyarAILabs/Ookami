"""Example customer evaluator: the customer's own definition of a correct answer."""
from ookami import Score, evaluator


@evaluator(name="task_check", version="1")
def score(example, output) -> Score:
    want = example.meta.get("resolved")
    text = (output or {}).get("content", "") if isinstance(output, dict) else str(output)
    ok = want is None or (("resolved" in text.lower()) == bool(want))
    return Score(float(ok), passed=ok, reason=None if ok else "resolution does not match the ticket system")
