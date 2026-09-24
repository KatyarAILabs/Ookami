# Evaluator plugins

Evaluators can ship as separate Python packages, so you can extend the gate without forking Forge.

## Writing one

Register a factory under the `forge.evaluators` entry point group:

```toml
# your package's pyproject.toml
[project.entry-points."forge.evaluators"]
"acme.refund_check" = "acme_forge:refund_check"
```

The factory receives `name`, `base_dir` (the directory of `forge.yaml`) and the parameters from the YAML. It returns an evaluator:

```python
# acme_forge/__init__.py
from forge import Score

class RefundCheck:
    kind = "plugin"
    version = "3"

    def __init__(self, name, base_dir, threshold=0.5):
        self.name, self.threshold = name, threshold

    def score(self, example, output) -> Score:          # a row evaluator
        ok = ...                                         # your logic, e.g. query your ledger read-only
        return Score(float(ok), passed=ok, reason=None if ok else "refund not posted")

def refund_check(name, base_dir, **params):
    return RefundCheck(name, base_dir, **params)
```

**Row or rollout:**
- A **row** evaluator implements `score(example, output)`.
- A **rollout** evaluator implements `run(target) -> (observations, notes)` and drives the model itself; see `evaluators/command.py`.

## Using it

```yaml
eval:
  evaluators:
    - plugin: { use: acme.refund_check, threshold: 0.7 }
```

- **Default name:** the evaluator's name defaults to the `use` value.
- **Missing plugin:** a `use` with no installed plugin is an error that lists the installed ones.
- **Version:** set `version` on the evaluator. It's recorded in every report, so results stay traceable when the evaluator changes.

## Other evaluator types

| Type | Use it when |
|---|---|
| `python` | You don't need a package: point at a file |
| `webhook` | The check runs in another service |
| `command` | The check is a benchmark harness in another language |
