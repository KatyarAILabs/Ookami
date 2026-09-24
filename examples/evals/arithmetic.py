"""A tiny Inspect task: exact arithmetic answers. Use as `- inspect: { task: evals/arithmetic.py }`."""
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import match
from inspect_ai.solver import generate

PAIRS = [(2, 2), (3, 5), (7, 8), (12, 30), (41, 17), (99, 1), (25, 25), (13, 29), (64, 36), (8, 9),
         (120, 45), (77, 23), (5, 6), (300, 12), (14, 14), (48, 52), (19, 81), (33, 44), (61, 9), (250, 250)]


@task
def arithmetic():
    return Task(
        dataset=[Sample(input=f"What is {a} + {b}? Reply with the number only.", target=str(a + b), id=f"{a}+{b}")
                 for a, b in PAIRS],
        solver=generate(),
        scorer=match(),
    )
