import pytest

from c3.judge import Judgment, Match
from c3.models import PullRequest, Reference
from c3.pooled import build_pool, human_precision, pooled_recall


def make_pr(dataset="D1"):
    return PullRequest("x/y#1", dataset, "x/y", 1, "", "", "a", "b", "", [
        Reference("h1", "A.java", 1, "h1", "human", True),
        Reference("h2", "A.java", 2, "h2", "human", True),
        Reference("h3", "A.java", 3, "h3", "human", True),
        Reference("a1", "A.java", 4, "a1", "ai", True),
        Reference("neg", "A.java", 5, "neg", "human", False),
    ])


def test_pool_deduplicates_cross_model_new_issue_clusters():
    pr = make_pr()
    pool = build_pool([pr], {"x/y#1": [
        Judgment("x/y#1", "claude", "diff", 1, "j", [Match("h1", 0)], 0),
        Judgment("x/y#1", "gpt", "diff", 1, "j", [Match("h2", 0)], 0),
    ]}, {"x/y#1": [[("claude", 3), ("gpt", 4)]]})
    novel = [issue for issue in pool if issue.ref_ids == []]
    assert len(novel) == 1
    assert novel[0].found_by == {"claude", "gpt"}
    assert pool[0].found_by == {"human", "claude"}


def test_pooled_recall_counts_issue_coverage():
    pr = make_pr()
    pool = build_pool([pr], {"x/y#1": [
        Judgment("x/y#1", "claude", "diff", 1, "j", [Match("h1", 0), Match("h2", 1), Match("a1", 2)], 0),
        Judgment("x/y#1", "gpt", "diff", 1, "j", [Match("h1", 0), Match("h3", 1)], 0),
    ]}, {"x/y#1": []})
    assert pooled_recall(pool, "human") == pytest.approx(3 / 4)
    assert pooled_recall(pool, "claude") == pytest.approx(3 / 4)
    assert pooled_recall(pool, "gpt") == pytest.approx(1 / 2)


def test_duplicate_or_validated_ai_indices_in_new_clusters_are_rejected():
    pr = make_pr()
    judgements = {"x/y#1": [Judgment("x/y#1", "claude", "diff", 1, "j", [Match("h1", 0)], 0)]}
    with pytest.raises(ValueError, match="validated"):
        build_pool([pr], judgements, {"x/y#1": [[("claude", 0)]]})
    with pytest.raises(ValueError, match="duplicate"):
        build_pool([pr], {}, {"x/y#1": [[("claude", 2)], [("claude", 2)]]})


def test_human_precision_uses_d1_expert_negative_candidates_only():
    assert human_precision([make_pr()]) == pytest.approx(3 / 4)
    assert human_precision([make_pr("D2")]) is None


def test_human_precision_is_none_without_negative_candidates():
    pr = make_pr()
    pr.references = pr.references[:-1]
    assert human_precision([pr]) is None


def test_pool_rejects_duplicate_reference_ids_within_pr():
    duplicate_pr = make_pr()
    duplicate_pr.references.append(duplicate_pr.references[0])
    with pytest.raises(ValueError, match="duplicate reference IDs"):
        build_pool([duplicate_pr], {}, {})


def test_same_reference_id_on_different_prs_remains_distinct():
    first = make_pr("D1")
    second = make_pr("D1")
    second.pr_id = "x/y#2"
    second.references = [Reference("shared", "A.java", 1, "first", "human", True)]
    first.references = [Reference("shared", "A.java", 1, "second", "human", True)]
    pool = build_pool([first, second], {}, {})
    assert [issue.issue_id for issue in pool] == ["x/y#1|shared", "x/y#2|shared"]
