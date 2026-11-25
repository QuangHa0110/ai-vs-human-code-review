import math

import pytest

from c3.annotation import (
    AnnotationItem, assign_raters, calibration_items, cohen_kappa,
    merge_labels, unmatched_items, validation_items,
)


def test_unmatched_items_excludes_matched_and_failed_artifacts_and_is_order_independent():
    prs = {"p1": {"pr_id": "p1", "dataset": "D1"}}
    reviews = {
        ("alice", "p1"): {
            "pr_id": "p1", "reviewer": "alice", "mode": "diff", "run_idx": 1,
            "fingerprint": "f1", "parse_error": False, "comments": [
                {"path": "A.java", "line": 2, "comment": "one", "category": "testing"},
                {"path": "B.java", "line": 3, "comment": "two", "category": "testing"},
            ],
        },
        ("bob", "p1"): {
            "pr_id": "p1", "reviewer": "bob", "mode": "diff", "run_idx": 1,
            "fingerprint": "f2", "error": "failed", "parse_error": False, "comments": [],
        },
    }
    judgments = {("alice", "p1"): {"matches": [{"ref_id": "r1", "ai_index": 0}]}}
    args = (prs, reviews, judgments, ["alice", "bob"], None, 42)
    items, private = unmatched_items(*args)
    reversed_items, reversed_private = unmatched_items(
        prs, dict(reversed(list(reviews.items()))), judgments, ["bob", "alice"], None, 42
    )
    assert items == reversed_items
    assert private == reversed_private
    assert len(items) == 1
    assert items[0].text == "two"
    assert set(items[0].__dataclass_fields__) == {"item_id", "pr_id", "path", "line", "text", "pr_url"}
    assert len(items[0].item_id) == 20 and all(c in "0123456789abcdef" for c in items[0].item_id)
    assert private[items[0].item_id]["reviewer"] == "alice"


def test_unmatched_items_d1_all_and_d2_sampled_per_reviewer():
    prs = {f"p{i}": {"pr_id": f"p{i}", "dataset": "D2"} for i in range(4)}
    reviews = {}
    judgments = {}
    for i in range(4):
        key = ("reviewer", f"p{i}")
        reviews[key] = {"pr_id": f"p{i}", "reviewer": "reviewer", "mode": "diff", "run_idx": 1,
                        "fingerprint": f"f{i}", "parse_error": False, "comments": [{"path": "A.java", "line": 1,
                        "comment": str(i), "category": "testing"}]}
        judgments[key] = {"matches": []}
    all_items, _ = unmatched_items(prs, reviews, judgments, ["reviewer"], 4, 5)
    sample, _ = unmatched_items(prs, reviews, judgments, ["reviewer"], 2, 5)
    assert len(all_items) == 4
    assert len(sample) == 2


@pytest.mark.parametrize("dataset", ["D1", "D2"])
def test_unmatched_items_scopes_d1_and_d2_to_run1_diff(dataset):
    prs = {"p1": {"pr_id": "p1", "dataset": dataset}}
    reviews, judgments = {}, {}
    for mode, run_idx in (("diff", 1), ("diff", 2), ("agent", 1)):
        key = ("reviewer", "p1", mode, run_idx)
        reviews[key] = {"pr_id": "p1", "reviewer": "reviewer", "mode": mode, "run_idx": run_idx,
                        "fingerprint": f"{mode}-{run_idx}", "parse_error": False,
                        "comments": [{"path": "A.java", "line": 1, "comment": f"{mode}-{run_idx}",
                                      "category": "testing"}]}
        judgments[key] = {"matches": []}
    items, private = unmatched_items(prs, reviews, judgments, ["reviewer"], 5 if dataset == "D2" else None, 7)
    assert len(items) == 1
    assert items[0].text == "diff-1"
    assert next(iter(private.values()))["run_idx"] == 1


def test_mixed_d1_d2_sampling_keeps_all_d1_and_caps_only_d2():
    prs = {"d1": {"pr_id": "d1", "dataset": "D1"}}
    reviews, judgments = {}, {}
    for pr_id, dataset in [("d1", "D1"), *((f"d2-{i}", "D2") for i in range(4))]:
        prs[pr_id] = {"pr_id": pr_id, "dataset": dataset}
        key = ("reviewer", pr_id)
        reviews[key] = {"pr_id": pr_id, "reviewer": "reviewer", "mode": "diff", "run_idx": 1,
                        "fingerprint": pr_id, "parse_error": False,
                        "comments": [{"path": "A.java", "line": 1, "comment": pr_id, "category": "testing"}]}
        judgments[key] = {"matches": []}
    items, _ = unmatched_items(prs, reviews, judgments, ["reviewer"], 2, 19)
    assert sum(item.pr_id == "d1" for item in items) == 1
    assert sum(item.pr_id.startswith("d2-") for item in items) == 2


def test_calibration_items_balances_positive_and_negative_reference_items_without_label_leakage():
    prs = [{"pr_id": "p1", "dataset": "D1", "references": [
        {"ref_id": "a", "valid": True, "path": "A.java", "line": 1, "text": "positive"},
        {"ref_id": "b", "valid": False, "path": "B.java", "line": 2, "text": "negative"},
    ]}]
    items, labels = calibration_items(prs, 2, 8)
    assert len(items) == 2
    assert {label for label in labels.values()} == {"valid", "noise"}
    assert all(set(item.__dataclass_fields__) == {"item_id", "pr_id", "path", "line", "text", "pr_url"}
               for item in items)
    with pytest.raises(ValueError, match="insufficient"):
        calibration_items(prs, 4, 8)


def test_validation_items_returns_blinded_expert_refs_and_private_hit_labels():
    prs = [{"pr_id": "p1", "dataset": "D1", "references": [
        {"ref_id": "r1", "valid": True, "path": "A.java", "line": 1, "text": "known issue"},
        {"ref_id": "r2", "valid": False, "path": "B.java", "line": 2, "text": "noise"},
    ]}]
    items, labels = validation_items(prs, {"judge": {"pr_id": "p1", "matches": [
        {"ref_id": "r1", "ai_index": 0}]}}, {"p1"})
    assert len(items) == 1
    assert labels == {f"p1|r1": True}
    assert "ref_id" not in items[0].__dict__


def test_assign_raters_balances_load_and_uses_two_distinct_raters():
    assigned = assign_raters(["i1", "i2", "i3", "i4", "i5"], ["r1", "r2", "r3"], 2)
    assert all(len(set(raters)) == 2 for raters in assigned.values())
    loads = [sum(r in raters for raters in assigned.values()) for r in ("r1", "r2", "r3")]
    assert max(loads) - min(loads) <= 1
    assert assigned == assign_raters(["i5", "i4", "i3", "i2", "i1"], ["r3", "r2", "r1"], 2)


def test_cohen_kappa_handles_perfect_single_item_empty_and_known_example():
    assert cohen_kappa(["valid"], ["valid"]) == 1
    assert math.isnan(cohen_kappa([], []))
    assert cohen_kappa(["valid", "valid", "noise", "noise"],
                       ["valid", "noise", "valid", "noise"]) == pytest.approx(0)


def test_merge_labels_requires_adjudication_for_disagreement_and_outputs_report_schema():
    keys = {"i1": {"dataset": "D1", "pr_id": "p1", "reviewer": "alice", "mode": "diff",
                    "run_idx": 1, "ai_index": 0, "review_fingerprint": "f1", "missed_by_human": None}}
    with pytest.raises(ValueError, match="adjudication"):
        merge_labels({"r1": {"i1": "valid"}, "r2": {"i1": "noise"}}, keys)
    result = merge_labels({"r1": {"i1": "valid"}, "r2": {"i1": "valid"}}, keys)
    assert result["schema"] == "c3-adjudication-v1"
    assert result["items"][0]["label"] == "valid"
    assert result["items"][0]["missed_by_human"] is None
