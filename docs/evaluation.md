# Evaluation and the gate

**Forge supplies the statistics and the gate. You supply the judgement.** Forge never claims a model is correct. It reports whether the candidate met *your* evaluators, with confidence bounds.

## Evaluators

| Kind | YAML | Shape | Judges |
|---|---|---|---|
| `labels` | `- labels: { column: label, match: exact }` | row | Output text vs a column in your data (`exact` or `contains`, case and whitespace ignored) |
| `python` | `- python: ./evals/check.py:score` | row | Your function `score(example, output) -> Score / bool / number / {value, passed, reason}` |
| `webhook` | `- webhook: { url: http://checker/score }` | row | Your service: POST `{example, output}` → `{value, passed?, reason?}` |
| `command` | `- command: { run: "python bench.py --base-url {base_url} --model {model} --out {output}" }` | rollout | Any harness that writes JSONL `{"item_id", "score", "passed"?, "slice"?, "error"?}` |
| `plugin` | `- plugin: { use: acme.check, ...params }` | either | An installed package (see [plugins](plugins.md)) |
| `forge/structural` | `- forge/structural` | row | **Shape only:** tool name exists, tool arguments match the schema, JSON output parses and matches `json_schema`. Labelled as such in reports |
| `lm-eval`, `inspect` | in the schema | rollout | Planned |

- **Row evaluators:** Forge produces the outputs itself, by calling the target with each held-out row (`eval.generation` sets max tokens and temperature), or by reading recorded outputs.
- **Rollout evaluators:** these drive the model themselves and define their own items. Several attempts per item (trials) are averaged.
- **Non-gating evaluators:** an evaluator with `gate: false` is reported but doesn't block promotion.

## Splits

`eval.splits`:

| Split | Default share | Used for |
|---|---|---|
| `heldOut` | 10% | Gating |
| `audit` | 5% | Gating. Never used for training or as an RL reward, so the gate always has an independent check |
| `train` | the rest | Training |

- **How splits are assigned:** hash-based per group of identical inputs, so duplicates never straddle splits and assignments are stable as data grows.
- **Slices:** `stratifyBy: [field, ...]` also judges each slice.

## Statistics

For each gating evaluator, split and slice:

1. **Pair by item.** Candidate and incumbent are scored on the same items. Per-item scores are averaged over trials. Items scored for only one side are left out and counted in the notes.
2. **Bootstrap.** Resample the paired differences `gate.resamples` times (default 10,000). This gives one-sided bounds at `gate.confidence` (default 95%).
3. **Test.**

| `gate.test` | Passes when |
|---|---|
| `non-inferiority` (default) | Lower bound of (candidate − incumbent) ≥ `margin` (default −0.01) |
| `superiority` | Lower bound > 0 |
| `threshold` | Candidate mean ≥ `min` |

- **Absolute floor:** `gate.min` also sets a floor for the other two tests.
- **Too few items:** fewer paired items than `gate.minItems` (default 20) can't pass.

**Decision:**

| Decision | When |
|---|---|
| `pass` | Every gating result passes |
| `partial` | The overall slice passes but some slices fail |
| `fail` | Otherwise |

After training, only `pass` sets a version to `passed`.

**The incumbent** is the live version, or the base model when nothing is live.

## Guardrails

**Warnings from `forge validate`:**
- The RL reward is also a gate evaluator. RL learns to exploit the reward's blind spots, and a gate using the same function can't see them.
- RL with `audit: 0`.
- The gate uses structural checks only.
- Shadow is configured without a `shadowExecutor`. Shadow scores are only valid for single-turn or read-only routes.

**Notes in the report:**
- The candidate and incumbent gave identical outputs on ≥95% of rows. This usually means the candidate endpoint isn't serving the trained weights.
- Items were dropped for being scored on only one side.
- Command evaluator attempts marked `error` were dropped as infrastructure failures, not counted as model failures.

**Deliberately not shipped:** "agreement with the incumbent" and built-in LLM-judge gates. Both measure imitation, not quality. If you want a judge, bring it as your own evaluator; it's labelled in reports.

## Reports

Every run writes `<storage>/reports/<model>/eval-<time>.json` and a `.md` alongside it. They contain:
- the decision;
- the targets;
- each result: evaluator, split, slice, n, both means, difference, bounds, pass/fail and reason;
- non-gating results;
- the evaluators with their versions;
- notes;
- provenance: config hash, dataset hash, version, incumbent.

After training, a report's path is stored on the registry version.
