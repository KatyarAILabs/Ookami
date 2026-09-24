"""Ookami: packaged, self-hosted AI infrastructure. Traffic in, gated fine-tuned models out."""
from .evaluators.base import Example, Score, evaluator

__version__ = "0.2.0.dev0"
__all__ = ["Example", "Score", "evaluator", "__version__"]
