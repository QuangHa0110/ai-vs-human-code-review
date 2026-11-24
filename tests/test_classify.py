import json
from copy import deepcopy

import pytest

from c3.models import CATEGORIES, PullRequest, Reference
from c3.runners import RunResult


class Classifier:
    def __init__(self, *results):
        self.results = iter(results)
        self.calls = []

    def run(self, system, user, workdir, allow_read_tools):
        self.calls.append((system, user, workdir, allow_read_tools))
        return next(self.results)


def make_pr(dataset="D2", number=1):
    return PullRequest(
        f"org/example#{number}", dataset, "org/example", number,
        "Title from PR", "Body from PR", "base", "head", "Review diff",
        [
            Reference("first", "Example.java", 12, "Check the missing null handling", "human", False),
            Reference("second", "Example.java", 18, "Thanks for documenting this", "human", True),
            Reference("automated", "Example.java", 19, "Auto-generated warning", "ai", False,
                      "Defect", False),
        ],
    )


LABELS = [
    {"ref_id": "second", "actionable": False, "category": "style_naming_docs"},
    {"ref_id": "first", "actionable": True, "category": "error_handling"},
]


def model_response(labels=LABELS):
    return RunResult(json.dumps(labels), {})


def test_classification_uses_one_tool_free_call_and_preserves_input():
    from c3.classify import classify_references

    pr = make_pr()
    original = deepcopy(pr)
    runner = Classifier(model_response())
    classified = classify_references(runner, pr)

    (system, user, workdir, allow_read_tools), = runner.calls
    assert workdir is None and allow_read_tools is False
    assert all(category in system for category in CATEGORIES)
    for context in (pr.title, pr.body, pr.diff, "first", "second", "Example.java"):
        assert context in user
    assert "Auto-generated warning" not in user
    assert [(ref.actionable, ref.category, ref.valid) for ref in classified.references] == [
        (True, "error_handling", False),
        (False, "style_naming_docs", True),
        (False, "Defect", False),
    ]
    assert pr == original
    assert classified.references[2] == pr.references[2]
    assert classified.references[2] is not pr.references[2]


def test_ai_only_pr_skips_runner():
    from c3.classify import classify_references

    pr = make_pr()
    pr.references = pr.references[2:]
    runner = Classifier()
    classified = classify_references(runner, pr)
    assert runner.calls == []
    assert classified == pr
    assert classified.references[0] is not pr.references[0]


@pytest.mark.parametrize(("response", "match"), [
    (RunResult("[malformed]", {}), "JSON"),
    (RunResult(json.dumps(LABELS), {}, "runner failure"), "runner failure"),
    (model_response({"labels": LABELS}), "list"),
    (model_response([LABELS[0]]), "Missing.*first"),
    (model_response([*LABELS, LABELS[0]]), "Duplicate.*second"),
    (model_response([*LABELS, {**LABELS[0], "ref_id": "automated"}]), "Unknown.*automated"),
    (model_response([LABELS[0], {**LABELS[1], "category": "Defect"}]), "category"),
    (model_response([LABELS[0], {**LABELS[1], "actionable": 1}]), "boolean"),
    (model_response([LABELS[0], {"ref_id": "first", "actionable": True}]), "fields"),
    (model_response([LABELS[0], {**LABELS[1], "explanation": "extra"}]), "fields"),
])
def test_invalid_classification_raises_without_modifying_original(response, match):
    from c3.classify import classify_references

    pr = make_pr()
    original = deepcopy(pr)
    with pytest.raises(ValueError, match=match):
        classify_references(Classifier(response), pr)
    assert pr == original


@pytest.mark.parametrize(("field", "value"), [("actionable", False), ("category", "testing")])
def test_duplicate_json_fields_are_rejected(field, value):
    from c3.classify import classify_references

    extra_field = json.dumps({field: value})[1:-1]
    duplicate_label = json.dumps(LABELS[1])[:-1] + ", " + extra_field + "}"
    response = RunResult(f"[{json.dumps(LABELS[0])}, {duplicate_label}]", {})
    with pytest.raises(ValueError, match=f"Duplicate classification JSON field: {field}"):
        classify_references(Classifier(response), make_pr())


@pytest.mark.parametrize("dataset", ["D1", "D3"])
def test_filters_human_references_without_minimum_for_d1_and_d3(dataset):
    from c3.classify import classify_references, keep_actionable

    classified = classify_references(Classifier(model_response()), make_pr(dataset))
    retained = keep_actionable(classified)
    assert retained.references == [classified.references[0], classified.references[2]]
    assert [ref.ref_id for ref in classified.references] == ["first", "second", "automated"]


def test_d2_minimum_counts_only_actionable_human_references():
    from c3.classify import classify_references, keep_actionable

    classified = classify_references(Classifier(model_response()), make_pr())
    assert keep_actionable(classified) is None
    assert keep_actionable(classified, min_refs=1).references == [
        classified.references[0], classified.references[2],
    ]
