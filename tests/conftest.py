import json
import textwrap
from pathlib import Path

import pytest

PLATFORM = """
apiVersion: ookami.dev/v1alpha1
kind: Platform
metadata: { name: t }
spec:
  storage: { uri: ./store }
"""


@pytest.fixture
def write(tmp_path: Path):
    def _write(name: str, text: str) -> Path:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text))
        return p
    return _write


def model_doc(eval_block: str, extra: str = "", data: str = "{ jsonl: data.jsonl }", train: str = "recipe: sft",
              base: str = "base: qwen3.5-4b") -> str:
    return PLATFORM + f"""---
apiVersion: ookami.dev/v1alpha1
kind: Model
metadata: {{ name: m }}
spec:
  {base}
  data: {{ source: {data} }}
  train: {{ {train} }}
  eval:
{textwrap.indent(textwrap.dedent(eval_block), '    ')}
{extra}
"""


def score_rows(path: Path, scores: dict[str, list[float]], errors: int = 0) -> Path:
    rows = [{"item_id": k, "score": s, "passed": s >= 1} for k, ss in scores.items() for s in ss]
    rows += [{"item_id": "x", "error": True}] * errors
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return path
