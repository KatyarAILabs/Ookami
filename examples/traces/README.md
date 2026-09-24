# Train on captured traffic with Trajectory

[Trajectory](https://github.com/KatyarAILabs/trajectory) captures agent traffic at training fidelity. Ookami runs its collector, wires the gateway to it, and fine-tunes on what it captured.

1. **Install Trajectory's `cc`** and set the `mapping:` path in `collector.yaml` to your checkout's `mappings/litellm.yaml`.
2. **Start everything:** `CC_HMAC_KEY=<secret> ookami up`. This starts the collector, the model and the gateway on :4000.
3. **Send traffic through the gateway.** For grouping, send:
   - `litellm_session_id`, one per agent run;
   - `metadata.task_type`;
   - `metadata.episode_end: true` on the last call.
4. **Label and score the episodes in Trajectory:** `cc outcomes`, then `cc score` (see Trajectory's `docs/OUTCOMES.md`).
5. **Train:** `ookami train assistant` exports the episodes scored at or above `minReward`, fine-tunes, and gates the result against the base model.

**Which parts are yours to edit:**
- `collector.yaml`: the redaction policy.
- `check.py`: what counts as a good answer for the gate.
