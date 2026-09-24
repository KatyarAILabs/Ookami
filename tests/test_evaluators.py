import json

from forge.evaluators.base import Example, Score, Target, as_score
from forge.evaluators.builtin import LabelsEvaluator, PythonEvaluator, StructuralEvaluator
from forge.evaluators.command import CommandEvaluator, parse_scores

from conftest import score_rows

TOOLS = [{"type": "function", "function": {"name": "refund", "parameters": {
    "type": "object", "required": ["order_id", "amount"],
    "properties": {"order_id": {"type": "string"}, "amount": {"type": "number"}}}}}]


def call(name, args):
    return {"role": "assistant", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def test_structural_tool_calls():
    ev = StructuralEvaluator()
    ex = Example("1", [], meta={"tools": TOOLS})
    assert ev.score(ex, call("refund", json.dumps({"order_id": "o1", "amount": 5}))).passed
    assert "unknown tool" in ev.score(ex, call("delete", "{}")).reason
    assert "not JSON" in ev.score(ex, call("refund", "{bad")).reason
    r = ev.score(ex, call("refund", json.dumps({"order_id": 3})))
    assert not r.passed and "should be string" in r.reason and "amount is required" in r.reason


def test_structural_json_output():
    ev = StructuralEvaluator()
    ex = Example("1", [], meta={"json_schema": {"type": "object", "required": ["a"]}})
    assert ev.score(ex, {"content": '{"a": 1}'}).passed
    assert not ev.score(ex, {"content": "nope"}).passed
    assert ev.score(Example("2", []), {"content": "free text"}).passed   # nothing to check


def test_labels():
    ev = LabelsEvaluator("l", column="label")
    assert ev.score(Example("1", "q", label="Yes"), {"content": " yes "}).passed
    assert not ev.score(Example("1", "q", label="yes"), "no").passed
    assert LabelsEvaluator("l", match="contains").score(Example("1", "q", label="4"), "it is 4").passed
    assert not ev.score(Example("1", "q"), "x").passed


def test_python_evaluator_loads_customer_code(tmp_path):
    (tmp_path / "ev.py").write_text(
        "from forge import evaluator\n@evaluator(version='7')\ndef check(example, output):\n    return output == 'ok'\n")
    ev = PythonEvaluator("c", ref="./ev.py:check", base_dir=tmp_path)
    assert ev.version == "7" and ev.score(Example("1", "q"), "ok").passed


def test_as_score_forms():
    assert as_score(True).value == 1.0
    assert as_score(0.5).passed is None
    assert as_score({"value": 0, "passed": False, "reason": "r"}).reason == "r"
    assert isinstance(as_score(Score(1.0)), Score)


def test_target_parse():
    t = Target.parse("openai:http://h:8000/v1/#qwen", "c")
    assert (t.base_url, t.model) == ("http://h:8000/v1", "qwen")
    assert Target.parse("results:/tmp/x.json", "c").kind == "results"


def test_command_scores_drop_errors_and_keep_slices(tmp_path):
    p = score_rows(tmp_path / "s.jsonl", {"a": [1.0, 0.0], "b": [1.0]}, errors=2)
    with p.open("a") as f:
        f.write('\n{"id": "a", "output": "ignored row for row evaluators"}\n')
        f.write(json.dumps({"item_id": "c", "score": 0.5, "slice": {"level": 2}}) + "\n")
    obs, notes = parse_scores(p)
    assert len(obs) == 4 and "dropped 2" in notes[0]
    assert obs[-1].slice == {"level": "2"}


def test_command_runs_live_against_a_target(tmp_path):
    (tmp_path / "bench.py").write_text(
        "import json, sys\n"
        "out, model = sys.argv[1], sys.argv[2]\n"
        "open(out, 'w').write(json.dumps({'item_id': 'q1', 'score': 1.0 if model == 'good' else 0.0}))\n")
    ev = CommandEvaluator("b", base_dir=tmp_path, run="python bench.py {output} {model}")
    obs, _ = ev.run(Target.parse("openai:http://x/v1#good", "candidate"))
    assert obs[0].score.value == 1.0
