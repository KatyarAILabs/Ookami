from pathlib import Path

from conftest import PLATFORM, model_doc

from forge.config import load

EVAL = """
evaluators:
  - labels: { column: resolved }
"""


def errors(cfg):
    return [str(i) for i in cfg.issues if i.level == "error"]


def warnings(cfg):
    return [str(i) for i in cfg.issues if i.level == "warning"]


def test_examples_validate():
    root = Path(__file__).parents[1] / "examples"
    for f in ("forge.yaml", "serve-only.yaml", "benchmark.yaml"):
        cfg = load(root / f)
        assert cfg.ok, cfg.issues


def test_minimal_model_gets_defaults(write):
    cfg = load(write("f.yaml", model_doc(EVAL)))
    assert cfg.ok, cfg.issues
    m = cfg.models["m"].spec
    assert m.eval.gate.test == "non-inferiority" and m.eval.splits.heldOut == 0.1
    assert m.eval.evaluators[0].name == "labels:resolved"
    assert m.handoff.stages[-1] == "live"


def test_unknown_key_is_an_error(write):
    cfg = load(write("f.yaml", PLATFORM.replace("storage:", "storag: { uri: x }\n  storage:")))
    assert any("storag" in e for e in errors(cfg))


def test_inline_secret_rejected(write):
    text = PLATFORM + "  gateway: { mode: external, url: http://gw, adminKey: sk-123 }\n"
    assert any("secret reference" in e for e in errors(load(write("f.yaml", text))))


def test_inline_db_credentials_rejected(write):
    text = PLATFORM + "  database: { url: 'postgres://u:p@h/db' }\n"
    assert any("secret" in e for e in errors(load(write("f.yaml", text))))


def test_k8s_needs_postgres_and_cloud_profile(write):
    text = PLATFORM.replace("storage:", "backend: k8s\n  storage:")
    assert any("compute.profile" in e for e in errors(load(write("f.yaml", text))))


def test_grpo_needs_reward(write):
    cfg = load(write("f.yaml", model_doc(EVAL, train="recipe: grpo")))
    assert any("needs train.reward" in e for e in errors(cfg))


def test_unknown_base_needs_allowed_licence(write):
    assert any("not in the catalog" in e for e in errors(load(write("a.yaml", model_doc(EVAL, base="base: llama-4")))))
    bad = load(write("b.yaml", model_doc(EVAL, base="base: llama-4\n  licence: Llama-4")))
    assert any("not allowed" in e for e in errors(bad))
    assert load(write("c.yaml", model_doc(EVAL, base="base: my-org/model\n  licence: MIT"))).ok


def test_overrides_whitelist(write):
    cfg = load(write("f.yaml", model_doc(EVAL, train="overrides: { optimizer: sgd }")))
    assert any("cannot override" in e for e in errors(cfg))


def test_stages_must_ascend_and_end_live(write):
    for stages, msg in [("[live, canary:10%]", "increase"), ("[shadow, canary:10%]", "end with live"),
                        ("[canary:0%, live]", "1-99%"), ("[shadow, blue, live]", "bad stage")]:
        cfg = load(write("f.yaml", model_doc(EVAL, extra=f"  handoff: {{ stages: {stages} }}")))
        assert any(msg in e for e in errors(cfg)), (stages, cfg.issues)


def test_rollback_metric_must_be_an_evaluator(write):
    cfg = load(write("f.yaml", model_doc(EVAL, extra="  handoff: { rollback: { metric: nope, below: 0.9 } }")))
    assert any("not an evaluator name" in e for e in errors(cfg))


def test_duplicate_evaluator_names(write):
    ev = """
    evaluators:
      - labels: { column: a }
        name: x
      - labels: { column: b }
        name: x
    """
    assert any("unique" in e for e in errors(load(write("f.yaml", model_doc(ev)))))


def test_splits_leave_room_to_train(write):
    ev = EVAL + "splits: { heldOut: 0.4, audit: 0.2 }\n"
    assert any("at least half" in e for e in errors(load(write("f.yaml", model_doc(ev)))))


def test_reward_reused_as_gate_warns(write):
    write("r.py", "def score(example, output):\n    return 1.0\n")
    ev = """
    evaluators:
      - python: ./r.py:score
    """
    cfg = load(write("f.yaml", model_doc(ev, train="recipe: grpo, reward: ./r.py:score")))
    assert cfg.ok
    assert any("exploit" in w for w in warnings(cfg))


def test_structural_only_gate_warns(write):
    cfg = load(write("f.yaml", model_doc("evaluators: [forge/structural]\n")))
    assert any("structural checks only" in w for w in warnings(cfg))


def test_shadow_without_executor_warns(write):
    cfg = load(write("f.yaml", model_doc(EVAL, extra="  handoff: { route: { model: gpt-x } }")))
    assert any("shadowExecutor" in w for w in warnings(cfg))


def test_missing_python_ref_is_an_error(write):
    ev = """
    evaluators:
      - python: ./nope.py:score
    """
    assert any("not found" in e for e in errors(load(write("f.yaml", model_doc(ev)))))


SERVE_ONLY = PLATFORM + """---
apiVersion: forge.dev/v1alpha1
kind: Model
metadata: { name: s }
spec:
  base: gpt-oss-20b
"""


def test_serve_only_model_needs_no_training(write):
    assert load(write("f.yaml", SERVE_ONLY)).ok


def test_training_needs_data_and_eval(write):
    text = SERVE_ONLY.replace("base: gpt-oss-20b", "base: gpt-oss-20b\n  train: { recipe: sft }")
    assert any("train needs data" in e for e in errors(load(write("f.yaml", text))))
    text = text.replace("train:", "data: { source: { hf: org/ds } }\n  train:")
    assert any("train needs eval" in e for e in errors(load(write("g.yaml", text))))


def test_model_needs_enabled_components(write):
    text = model_doc(EVAL).replace("storage: { uri: ./store }",
                                   "storage: { uri: ./store }\n  components: { training: { enabled: false } }")
    assert any("components.training" in e for e in errors(load(write("f.yaml", text))))


def test_route_needs_a_gateway(write):
    text = model_doc(EVAL, extra="  handoff: { route: { model: gpt-x } }").replace(
        "storage: { uri: ./store }", "storage: { uri: ./store }\n  gateway: { mode: none }")
    assert any("needs a gateway" in e for e in errors(load(write("f.yaml", text))))


def test_traces_source_needs_a_lake(write):
    cfg = load(write("f.yaml", model_doc(EVAL, data="{ traces: { minReward: 1 } }")))
    assert any("needs a lake" in e for e in errors(cfg))
    assert load(write("g.yaml", model_doc(EVAL, data="{ traces: { lake: ./lake } }"))).ok


def test_tracing_component_needs_collector(write):
    on = PLATFORM + "  components: { tracing: { enabled: true } }\n"
    assert any("Platform.tracing" in e for e in errors(load(write("a.yaml", on))))
    managed = on + "  tracing: { lake: ./lake, config: ./missing.yaml }\n"
    assert any("tracing.config not found" in e for e in errors(load(write("b.yaml", managed))))
    assert any("tracing.config" in e for e in errors(load(write("c.yaml", on + "  tracing: { lake: ./lake }\n"))))


def test_external_gateway_needs_url(write):
    assert any("gateway.url" in e for e in errors(load(write("f.yaml", PLATFORM + "  gateway: { mode: external }\n"))))


def test_provider_models(write):
    api = PLATFORM + """---
apiVersion: forge.dev/v1alpha1
kind: Model
metadata: { name: gpt }
spec: { provider: { name: openai, model: gpt-5-mini, apiKey: "${secret:OPENAI_API_KEY}" } }
"""
    assert load(write("a.yaml", api)).ok
    assert any("secret reference" in e for e in errors(load(write("b.yaml", api.replace('"${secret:OPENAI_API_KEY}"', "sk-live")))))
    both = api.replace("spec: { provider:", "spec: { base: qwen3.5-4b, provider:")
    assert any("exactly one of base" in e for e in errors(load(write("c.yaml", both))))


def test_auth_none_warns(write):
    cfg = load(write("f.yaml", PLATFORM + "  gateway: { auth: none }\n"))
    assert any("anyone who can reach" in w for w in warnings(cfg))
