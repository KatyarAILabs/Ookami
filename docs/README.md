# Ookami documentation

Ookami is open-source AI infrastructure that you host yourself, shipped as one package and configured by one `ookami.yaml`. It covers:
- a gateway;
- model serving;
- fine-tuning;
- eval gates;
- a model registry.

Parts are still planned: traffic capture, rollout (hand-off), Kubernetes, and GPU autoscaling. It runs on one machine today; Kubernetes and cloud backends come next.

| Doc | What it covers |
|---|---|
| [Getting started](getting-started.md) | Install, serve a model, fine-tune one, put it live |
| [Architecture](architecture.md) | Components, the four interfaces, the model lifecycle, what lands on disk |
| [Configuration reference](reference/ookami-yaml.md) | Every `ookami.yaml` field (generated from the code) |
| [CLI](cli.md) | Every command, its flags and exit codes |
| [Serving](serving.md) | Engines (vLLM, MLX, any command), the managed gateway, serving trained versions |
| [Training](training.md) | Snapshots, the memory planner, trainers, the job queue, checkpoints |
| [Evaluation and the gate](evaluation.md) | Evaluators, splits, statistics, guardrails, reports |
| [Evaluator plugins](plugins.md) | Shipping evaluators as separate packages |
| [Deployment](deployment.md) | One machine, a Kubernetes pod, an AWS spot GPU |
| [Verification log](verification.md) | What has been tested where, the results, and known issues |
| [Roadmap and design plan](plan.md) | Why Ookami exists, milestones, open decisions |
| [Research: the best packaged AI infra layer (Sep 2026)](research/2026-09-ai-infra-landscape.md) | Who packages AI infra, what teams need, best component per layer, where Ookami stands |
