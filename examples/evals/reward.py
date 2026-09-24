"""Example RL reward. Kept separate from the gate evaluator so GRPO can't game the gate."""
from forge import evaluator


@evaluator(name="reward", version="1")
def score(example, output) -> float:
    text = (output or {}).get("content", "") if isinstance(output, dict) else str(output)
    return 1.0 if text.strip() else 0.0
