# The best packaged AI infrastructure layer: research (Sep 2026)

2026-09-25. Three research passes: who packages AI infra today, what teams need, and the best open-source component for each layer. Licences and releases were checked on GitHub on 2026-09-25. **[U]** marks claims from vendor blogs or secondary sources.

## The answer in brief

- **No open-source, vendor-neutral package covers the whole stack.** The closest are:
  - **NVIDIA NeMo Helix:** Apache-2.0, v0.6, NVIDIA-centric, still 0.x.
  - **Red Hat OpenShift AI:** requires OpenShift and a subscription.
  - Everything else is one layer (Ollama, LiteLLM, vLLM, Langfuse) or managed only (Databricks, W&B+CoreWeave, Modal, Together/Fireworks).
- **Teams don't lack hardware; they lack simple operations.**
  - Complexity, maintenance and security are the top blockers.
  - GPU utilisation is poor: most organisations run below 70% at peak, and some vendor telemetry says about 5% [U].
  - Only 7% of organisations deploy models daily.
- **The projects that win:**
  - reach first value in minutes (Ollama);
  - give the whole product away and charge for governance (Langfuse);
  - keep dependencies few (PostHog dropped Helm over support cost);
  - keep the config short (Dify's 100+ env vars are a standing complaint).
- **So the best packaged layer is:**
  1. first call in about 5 minutes on one box;
  2. the same config up to Kubernetes and air-gapped sites;
  3. secure by default;
  4. SSO and multi-tenancy **free**;
  5. GPU efficiency built in;
  6. cost attribution across API and self-hosted models;
  7. the closed improvement loop (traces → fine-tune → eval gate → promote), which **nobody** ships open-source.

  Ookami has #1 partly and #7 largely. The rest is the roadmap below.

## 1. What teams need

| Signal | Evidence |
|---|---|
| Developers use open models, but tooling holds them back | 79% of developers use open models. Blockers: compute cost 27%, security and compliance 26%, maintenance 24%, deployment and scaling 23%. The report's conclusion: "tooling, not budget" ([State of Open Source AI 2026](https://stateofopensource.ai/)) |
| Enterprises mostly buy | 76% of AI use cases are bought rather than built. Open models' share of enterprise workloads fell from 19% to 13% in 2025, citing deployment complexity ([Menlo](https://menlovc.com/perspective/2025-mid-year-llm-market-update/), [Menlo Dec 2025](https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/)) |
| GPU waste is the measurable pain | 44% assign GPUs by hand or have no strategy. Cost control is the #1 challenge (53%) ([ClearML 2025–26](https://www.accessnewswire.com/newsroom/en/computers-technology-and-internet/new-clearml-report-reveals-cost-and-governance-concerns-dominate-1118009)). Over 75% run below 70% utilisation at peak ([ClearML 2024](https://go.clear.ml/the-state-of-ai-infrastructure-at-scale-2024)) |
| Kubernetes is the substrate, but teams rarely deploy | 66% of organisations hosting GenAI use Kubernetes for inference, but only 7% deploy models daily ([CNCF](https://www.cncf.io/announcements/2026/01/20/kubernetes-established-as-the-de-facto-operating-system-for-ai-as-production-use-hits-82-in-2025-cncf-annual-cloud-native-survey/)) |
| Hybrid is the norm | Teams split work between closed APIs and open models, so the gateway must route to both |
| Agent projects die without evidence | Gartner: over 40% of agentic AI projects cancelled by end of 2027 ([Gartner](https://www.gartner.com/en/newsroom/press-releases/2025-06-25-gartner-predicts-over-40-percent-of-agentic-ai-projects-will-be-canceled-by-end-of-2027)). Eval gates and cost attribution are how teams prove value |

**Table stakes in 2026** (missing any one disqualifies a platform):
- an OpenAI-compatible API with multi-provider routing;
- **auth, keys, budgets and rate limits in front of every model** (vLLM ships none);
- OTel GenAI traces;
- a trace and eval UI;
- SSO, RBAC, audit and per-team cost;
- MCP support.

**Differentiators**, which few ship packaged:
- **GPU efficiency:** scale-to-zero, fractional GPUs, multi-LoRA, KV/prefix-aware routing. llm-d reports about 3× tokens/s and 2× lower TTFT.
- **The closed improvement loop.**
- Agent sandboxes.
- Air-gap and PII guardrails.
- Honest cost break-even reporting.

## 2. Who packages AI infra today

| Product | Covers | Install | Licence | Biggest weakness |
|---|---|---|---|---|
| **NVIDIA NeMo Helix** | Gateway, SFT/DPO/GRPO, evals, guardrails, traces, Gym sandboxes, Studio | `uv tool install`, Docker, Helm | Apache-2.0 | 0.x (v0.6, 23 Sep 2026). NVIDIA-centric; GRPO needs K8s + Ray; embedded ClickHouse is dev-only. **The closest threat** ([notes](https://docs.nvidia.com/nemo-helix/documentation/reference/release-notes/current-release/)) |
| **Red Hat OpenShift AI 3.x** | Gateway (MaaS with cost attribution), vLLM + llm-d, training, evals, guardrails, Kueue, registry | Operator, disconnected | Paid on OpenShift | Needs OpenShift; the licence can cost more than the infra ([RH](https://www.redhat.com/en/blog/red-hat-ai-3-delivers-speed-accelerated-delivery-and-scale)) |
| NVIDIA AI Enterprise / NIM / Dynamo | Serving, customiser, evals, guardrails | NIM Operator | Proprietary, ~$4.5k/GPU/year | Lock-in |
| Kubeflow | Serving (KServe), Trainer v2, Katib, registry | Kustomize/Helm | Apache-2.0 (CNCF graduated) | Days to run, weeks to make reliable; no gateway, evals or traces |
| TrueFoundry (+ Seldon) | Gateway, MCP, serving, fine-tuning, guardrails | SaaS control plane + VPC data plane | Proprietary | Closed control plane |
| SkyPilot / dstack | Compute across clouds; serving; SSO (SkyPilot) | CLI + server | Apache / MPL | Compute layer only |
| GPUStack | Serving across 9 accelerator vendors, API keys, metering, UI | pip/Docker | Apache-2.0 | No training, evals or traces |
| Ollama / LocalAI | Single-node serving | Binary/Docker | MIT | No multi-tenancy |
| Unsloth (76.7k stars) | Desktop app and web UI to run and train models; fast single-GPU LoRA/RL; OpenAI-compatible API; `unsloth start` for coding agents | Desktop installers, `install.sh`, Docker | Core Apache-2.0; **Studio AGPL-3.0** | Personal, single-user: no gateway auth or budgets, no eval-gated promotion or registry. Complementary: its core could be an Ookami trainer |
| Anyscale / Ray | Serving, training, RL | SaaS/BYOC | Ray Apache; Anyscale paid | A framework, not a product |
| Databricks, W&B+CoreWeave, Nebius, Modal, Together/Fireworks | Up to the full loop | Managed | Proprietary | Not self-hostable or vendor-neutral |
| Future AGI / Langfuse | Gateway, evals, observability, guardrails | Docker/Helm | Apache / MIT | No serving, training or GPUs |
| AIBrix / KServe + llm-d | Inference, LoRA management, autoscaling | Helm | Apache-2.0 | Inference only |
| AMD Enterprise AI Suite | Serving, training, quotas | Helm | OSS | AMD only |

Sources: [comparison research, Sep 2026](#sources).

**Gaps nobody fills in one open-source, vendor-neutral package:**
1. The full loop, from traces through to a gateway traffic shift.
2. One config from a single box up to Kubernetes and air-gapped.
3. Eval gates as deployment policy, blocking promotion and canary.
4. Cost attribution tied to self-hosted GPU cost. Only OpenShift MaaS does this.
5. Training, serving and gateway on any hardware vendor.
6. Self-hosted RL with sandboxes.
7. SSO and multi-tenancy in the free tier. Of the full platforms, only SkyPilot ships SSO in its open-source edition.

**Consolidation keeps shrinking the independent options:**

| Acquirer | Bought |
|---|---|
| Datadog | Adaptive ML |
| Palo Alto Networks | Portkey |
| OpenAI | Promptfoo |
| ClickHouse | Langfuse |
| Modular | BentoML |
| TrueFoundry | Seldon |
| Rubrik | Predibase |
| CoreWeave | OpenPipe |

Users are wary of licence changes and roadmap capture. A foundation-neutral, Apache-2.0 project is itself an advantage.

## 3. Best open-source component per layer (what Ookami bundles)

| Layer | Default | Alternative | Licence | Notes |
|---|---|---|---|---|
| Serving | vLLM (+ LMCache), llama.cpp (CPU/edge), MLX (Mac) | SGLang (agent prefix reuse), Dynamo (large NVIDIA fleets) | Apache/MIT | vLLM v0.30, Sep 2026. **TGI is archived** (Dec 2025) |
| Serving on K8s | llm-d | KServe | Apache-2.0 | CNCF Sandbox; KV-aware routing, P/D, scale-to-zero |
| Gateway | LiteLLM (MIT part only, pinned) | Bifrost (Go), Agent Router (ex-Envoy AI Gateway) | MIT/Apache | **LiteLLM 1.82.7/1.82.8 were backdoored on PyPI (Mar 2026)**: Ookami requires ≥1.83. SSO/RBAC/audit are in LiteLLM's paid `enterprise/`, so Ookami builds its own. **TensorZero is archived** |
| Semantic routing | vLLM Semantic Router | - | Apache-2.0 | Optional |
| Traces | Trajectory + OTel GenAI | OpenLLMetry | Apache-2.0 | OTel GenAI semconv is still "Development" but stable in shape since 1.37 |
| Trace/eval UI | Langfuse (MIT core, no `ee/`) | Opik | MIT/Apache | **Exclude Arize Phoenix (ELv2)** |
| Eval evaluators | Ookami gate + Inspect + lm-eval-harness | DeepEval | MIT/Apache | promptfoo is now owned by OpenAI |
| Training | TRL (1.x) / PEFT, mlx-lm | Axolotl, Unsloth core | Apache/MIT | **Never Unsloth Studio (AGPL)**. **torchtune is discontinued** |
| RL | verl | SkyRL, ART (fit the Trajectory lake) | Apache-2.0 | |
| Scheduling | Kueue + KEDA + GPU Operator + DRA (K8s ≥1.34) | KAI Scheduler, HAMi (fractional GPU) | Apache-2.0 | Karpenter is cloud-only |
| Registry / artefacts | Ookami registry + ModelPack OCI (KitOps) | MLflow 3 | Apache-2.0 | KServe and Harbor pull OCI models natively |
| Vector (integration only) | pgvector | LanceDB, Qdrant | PostgreSQL/Apache | **Avoid Weaviate**: a proprietary `wl/` directory appeared in Aug 2026 |
| Agent sandboxes | k8s agent-sandbox + gVisor | Kata; E2B self-host (needs Nomad) | Apache-2.0 | |
| MCP gateway | IBM ContextForge | Agent Router MCPRoute | Apache-2.0 | |
| Guardrails / PII | Presidio + NeMo Guardrails | LlamaFirewall (code only) | MIT/Apache | Llama-licensed guard models are optional, never bundled |

**Never bundle:**
- Phoenix (ELv2)
- Unsloth Studio (AGPL)
- LiteLLM `enterprise/` and Langfuse `ee/`
- Weaviate (licence-switch risk)
- Llama-licensed weights
- archived or discontinued projects: TGI, TensorZero, torchtune

## 4. What the winners teach

| Lesson | From |
|---|---|
| First value in under 5 minutes beats features. Monetise after adoption | Ollama: ~8.9M monthly users, a 14-person team, cloud offering came later ([TechCrunch](https://techcrunch.com/2026/07/09/popular-open-source-ai-developer-tool-ollama-raises-65m-grows-to-nearly-9m-users/)) |
| Give the whole product away; charge only for SCIM, audit and retention | Langfuse moved nearly everything to MIT in Jun 2025 ([blog](https://langfuse.com/blog/2025-06-04-open-sourcing-langfuse-product)) |
| One command onto hardware people already own | Coolify |
| Launch cadence compounds | Supabase launch weeks |
| Choose the licence early | Airbyte's later move to ELv2 cost trust |
| Few datastores, and decide which install targets are really supported | PostHog sunset Helm after ~3.5% of users ran it on K8s ([blog](https://posthog.com/blog/sunsetting-helm-support-posthog)) |
| A short, validated config with generated secrets, not a giant `.env` | Dify's "5 hours instead of 5 minutes" ([issue](https://github.com/langgenius/dify/issues/33051)) |
| Many loosely integrated components lose to tools that are each easy | Kubeflow complaints [U] |
| Publish reliability benchmarks | LiteLLM production complaints about cold starts, leaks and p99 [U]; Future AGI publishes gateway numbers |

## 5. Where Ookami stands

| Requirement | Ookami today |
|---|---|
| First call in ~5 min on one box | **Partly.** `ookami up` works; install still means uv, extras and an engine |
| Same config to K8s / air-gap | **No.** Local backend only; K8s kits are pods, not a backend |
| Auth, keys, budgets, rate limits by default | **No.** The managed gateway runs without a master key (bound to localhost) |
| SSO / RBAC / multi-tenancy, free | **No** |
| Cost attribution (API + GPU) | **No** |
| OTel traces + trace UI | **Partly.** Trajectory captures; no Langfuse wiring |
| Eval gate as deployment policy | **Yes** for promotion; canary gating planned |
| Closed loop (traces → train → gate → promote → serve) | **Yes**, verified on Apple silicon; NVIDIA path pending |
| GPU efficiency (scale-to-zero, fractional, multi-LoRA, cache-aware routing) | **No**, apart from the gate's multi-LoRA vLLM serving |
| Hardware breadth | NVIDIA (vLLM, untested), Apple (MLX, tested), CPU/anything (command) |
| MCP, sandboxes, guardrails | **No** |
| Licence-clean bundle | **Yes** (Apache/MIT/BSD only) |

## 6. Recommended direction

**Positioning:** *the open, vendor-neutral AI infrastructure layer. It serves any model on any hardware behind one secure gateway, and it's the only one that turns your traffic into better models you own.* Lead with the loop, and win adoption with the first five minutes.

**Main risk: NeMo Helix.** It ships monthly under Apache-2.0 with NVIDIA's reach. Ookami's answer is to be:
- hardware-neutral;
- single-box-first;
- stable before Helix reaches 1.0;
- honest about what's verified.

Watch its releases every month.

The revised milestones are in [plan.md](../plan.md#milestones).

## Sources

**Surveys:**
- State of Open Source AI 2026: https://stateofopensource.ai/
- Menlo, mid-2025 LLM market update: https://menlovc.com/perspective/2025-mid-year-llm-market-update/
- Menlo, state of generative AI in the enterprise (Dec 2025): https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/
- a16z, enterprise AI 2025: https://a16z.com/ai-enterprise-2025/
- ClearML 2024: https://go.clear.ml/the-state-of-ai-infrastructure-at-scale-2024
- CNCF annual survey: https://www.cncf.io/announcements/2026/01/20/kubernetes-established-as-the-de-facto-operating-system-for-ai-as-production-use-hits-82-in-2025-cncf-annual-cloud-native-survey/

**Platforms:**
- NeMo Helix release notes: https://docs.nvidia.com/nemo-helix/documentation/reference/release-notes/current-release/
- Red Hat OpenShift AI 3: https://www.redhat.com/en/blog/red-hat-ai-3-delivers-speed-accelerated-delivery-and-scale
- Kubeflow at CNCF: https://www.cncf.io/blog/2026/07/28/kubeflow-unveils-new-cloud-native-innovations-to-supercharge-ai/
- TrueFoundry acquires Seldon: https://siliconangle.com/2026/06/25/truefoundry-acquires-mlops-pioneer-seldon-ai-accelerate-enterprise-agentic-ai/
- SkyPilot v0.10.0: https://github.com/skypilot-org/skypilot/releases/tag/v0.10.0
- GPUStack v2.2.2: https://newreleases.io/project/github/gpustack/gpustack/release/v2.2.2
- Datadog acquires Adaptive ML: https://www.datadoghq.com/blog/datadog-acquires-adaptive-ml/
- Palo Alto Networks acquires Portkey: https://www.paloaltonetworks.com/company/press/2026/palo-alto-networks-completes-acquisition-of-portkey-to-secure-ai-agents
- Future AGI open source: https://futureagi.com/blog/future-agi-q2-2026-open-source/

**Components:**
- llm-d joins CNCF: https://www.cncf.io/blog/2026/03/24/welcome-llm-d-to-the-cncf-evolving-kubernetes-into-sota-ai-infrastructure/
- LiteLLM security update (Mar 2026): https://docs.litellm.ai/blog/security-update-march-2026
- TGI docs: https://huggingface.co/docs/text-generation-inference/index
- Agent Router release notes: https://theagentrouter.ai/release-notes/
- Arize Phoenix licence: https://github.com/Arize-ai/phoenix/blob/main/LICENSE
- Unsloth Studio licence: https://github.com/unslothai/unsloth/blob/main/studio/LICENSE.AGPL-3.0
- torchtune discontinued: https://github.com/meta-pytorch/torchtune/issues/2883
- Kubernetes 1.36 DRA updates: https://kubernetes.io/blog/2026/05/07/kubernetes-v1-36-dra-136-updates/
- KServe OCI storage: https://kserve.github.io/website/docs/model-serving/storage/providers/oci
- TRL v1: https://huggingface.co/blog/trl-v1

**Lessons:**
- Ollama funding: https://techcrunch.com/2026/07/09/popular-open-source-ai-developer-tool-ollama-raises-65m-grows-to-nearly-9m-users/
- Langfuse open-sourcing: https://langfuse.com/blog/2025-06-04-open-sourcing-langfuse-product
- PostHog sunsetting Helm: https://posthog.com/blog/sunsetting-helm-support-posthog
- Dify self-hosting issue: https://github.com/langgenius/dify/issues/33051
- Airbyte move to ELv2: https://airbyte.com/blog/move-to-elv2
- Gartner on agentic AI: https://www.gartner.com/en/newsroom/press-releases/2025-06-25-gartner-predicts-over-40-percent-of-agentic-ai-projects-will-be-canceled-by-end-of-2027
