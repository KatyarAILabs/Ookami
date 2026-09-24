# CLI

Every command reads `forge.yaml` from the current directory, or from `-f FILE`.

**Exit codes, the same for all commands:**
- `0`: success, or the gate passed;
- `1`: error;
- `3`: the gate did not pass, a promotion was refused, or a service isn't ready.

| Command | What it does |
|---|---|
| `forge validate [-f FILE]` | Parses every document and reports all errors and warnings at once |
| `forge schema` | Prints the JSON Schema for editor autocomplete |
| `forge up [-f FILE] [--timeout S]` | Starts one engine per Model (serving its live version if there is one) and the managed gateway, in the background. Waits up to `--timeout` seconds (default 900) for each |
| `forge status [-f FILE]` | Lists services with ready / starting / exited. Exit 3 if any isn't ready |
| `forge logs NAME [-f FILE] [-n LINES]` | Last lines of a service's log (`gateway` or a Model name) |
| `forge down [-f FILE]` | Stops everything `forge up` started |
| `forge train MODEL [-f FILE] [--detach]` | Snapshots data, plans, queues a job; the worker trains, then gates. Without `--detach` it follows the job to the end |
| `forge jobs [-f FILE] [--logs JOB_ID] [-n LINES]` | Lists training jobs, or prints one job's trainer log |
| `forge models [MODEL] [-f FILE]` | Lists versions: status, gate decision, dataset hash, report path |
| `forge promote MODEL [VERSION] [-f FILE] [--force --reason TEXT]` | Makes a version live. Default: the newest that passed. Refuses a version that didn't pass unless `--force` with a `--reason`, which is recorded. Restarts the running engine |
| `forge eval --candidate T --incumbent T [--model M] [-f FILE]` | Runs the Model's evaluators on two targets and applies the gate |

**Eval targets:**

| Form | Means |
|---|---|
| `openai:<base_url>#<model>` | A live OpenAI-compatible endpoint, e.g. `openai:http://localhost:8000/v1#qwen` |
| `results:<path>` | Replay recorded outputs. For row evaluators: JSONL of `{"id", "output"}`. For `command` evaluators: JSONL of `{"item_id", "score"}` |

`forge _worker` is internal: it's the background queue worker that `forge train` starts.
