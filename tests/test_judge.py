import json

import pytest

from c3.judge import Judgment, Match, build_judge_prompt, judge_pr, validation_subset
from c3.models import AiComment, PullRequest, Reference
from c3.review import ReviewResult
from c3.runners import RunResult


def make_pr():
    return PullRequest("org/example#1", "D1", "org/example", 1, "title", "body",
                       "a" * 40, "b" * 40, "diff", [
                           Reference("r1", "a.py", 4, "human ref", "human", True),
                           Reference("r2", "b.py", 7, "invalid ref", "human", False),
                           Reference("r3", "c.py", None, "unknown ref", "human", None),
                       ])


def make_review(comments=None):
    return ReviewResult("org/example#1", "reviewer", "diff", 1,
                        comments if comments is not None else [AiComment("a.py", 4, "AI comment", "testing")],
                        {})


class FakeRunner:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def run(self, system, user, workdir, allow_read_tools):
        self.calls.append((system, user, workdir, allow_read_tools))
        return self.response


def test_judge_prompt_only_contains_valid_human_reference_and_ai_comments():
    system, user = build_judge_prompt(make_pr(), make_review())

    assert "r1" in user and "human ref" in user
    assert "r2" not in user and "invalid ref" not in user
    assert "r3" not in user and "unknown ref" not in user
    assert "AI comment" in user
    assert "Return only a JSON array" in system


def test_judge_pr_deduplicates_pairs_and_drops_out_of_range_pairs():
    runner = FakeRunner(RunResult('[["r1",0],["r1",0],["r1",99],["missing",0],["r2",0]]', {}))

    result = judge_pr(runner, make_pr(), make_review(), "judge")

    assert result == Judgment("org/example#1", "reviewer", "diff", 1, "judge",
                              [Match("r1", 0)], 3)
    assert runner.calls[0][3] is False


def test_judge_counts_malformed_pairs_but_allows_many_to_one_matches():
    runner = FakeRunner(RunResult('[["r1",0],["r1",1],["bad",0],["r1",true]]', {}))

    result = judge_pr(runner, make_pr(), make_review([
        AiComment("a.py", 4, "AI comment", "testing"),
        AiComment("b.py", 5, "Another AI comment", "testing"),
    ]), "judge")

    assert result.matched_ref_ids == {"r1"}
    assert result.matched_ai_indices == {0, 1}
    assert result.invalid == 2


@pytest.mark.parametrize("payload", [
    '{"matches":[]}', '[["r1",0,1]]', '[{"ref_id":"r1","ai_index":0}]',
    '[["r1",0],]', '[["r1",0],["r1",0]] trailing',
])
def test_judge_rejects_non_strict_json_outputs(payload):
    with pytest.raises(ValueError, match="judge output"):
        judge_pr(FakeRunner(RunResult(payload, {})), make_pr(), make_review(), "judge")


def test_judge_runner_failure_is_an_error_not_an_empty_judgment():
    with pytest.raises(ValueError, match="runner"):
        judge_pr(FakeRunner(RunResult("[]", {}, "secret runner diagnostic")),
                 make_pr(), make_review(), "judge")


def test_empty_ai_review_skips_runner():
    runner = FakeRunner(RunResult("should not be used", {}))

    result = judge_pr(runner, make_pr(), make_review([]), "judge")

    assert result.matches == []
    assert runner.calls == []


def test_validation_subset_is_deterministic_and_sorted():
    ids = [f"org/repo#{index}" for index in range(25, 0, -1)]

    selected = validation_subset(ids, 0.2, seed=19)

    assert selected == sorted(selected)
    assert selected == validation_subset(ids, 0.2, seed=19)
    assert len(selected) == 5
