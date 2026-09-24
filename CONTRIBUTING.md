# Contributing to Ookami

Thanks for helping. Ookami aims to be infrastructure people can run for years, so we value small, tested changes.

## Setup

```bash
uv sync --extra gateway
uv run pytest                                      # about 20 s; no GPU or network needed
uv run python scripts/gen_config_reference.py      # after changing src/ookami/config.py
```

## Ground rules

- **Tests with every change.** The test suite fakes engines and trainers (`tests/fake_server.py`,
  `tests/fake_trainer.py`), so most behaviour can be tested without a GPU. If a change touches a real engine
  or trainer, say in the PR where you ran it, and add the result to `docs/verification.md`.
- **The schema is the source of truth.** A new `ookami.yaml` field needs a `description=`, a default (unless
  it truly must be set), validation, and a regenerated `docs/reference/ookami-yaml.md`. CI checks this.
- **Unknown config is an error, never ignored.**
- **Ookami doesn't judge correctness.** Evaluators come from users. Don't add built-in scores that measure
  agreement with the old model and present them as quality.
- **Licences:** dependencies must be Apache-2.0, MIT or BSD. Anything else needs discussion first.
- **Commits:** one logical change each, with a message that says why. Sign off your commits
  (`git commit -s`): we use the [Developer Certificate of Origin](https://developercertificate.org/).

## Where things are

See [docs/architecture.md](docs/architecture.md#source-layout).

## Reporting bugs

Open an issue with:
- your `ookami.yaml`, with secrets removed;
- the command and its output;
- `ookami --version`;
- your OS, accelerator and engine version.

For security issues, see [SECURITY.md](SECURITY.md).
