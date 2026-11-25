import json

import pytest

from c3.config import ReviewerSpec
from c3.models import AiComment, PullRequest, Reference
from c3.runners import CachedRunner, RunResult


def make_pr(dataset="D1", number=1):
    refs = [
        Reference("first", "Main.java", 10, "UNIQUE REFERENCE", "human", True,
                  "logic_bug", True),
        Reference("second", "Main.java", 11, "SECOND REFERENCE", "human", True,
                  "error_handling", True),
    ]
    return PullRequest(
        f"org/example#{number}", dataset, "org/example", number, "UNIQUE TITLE",
        "UNIQUE BODY", "a" * 40, "b" * 40, "diff --git a/Main.java b/Main.java",
        refs,
    )


class FakeRunner:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []
        self.spec = ReviewerSpec("claude", "claude_cli", "fixture-model")
        self.tool_version = "fixture-version"

    def run(self, system, user, workdir, allow_read_tools):
        self.calls.append((system, user, workdir, allow_read_tools))
        if workdir is not None and not allow_read_tools:
            assert not list(workdir.iterdir())
        return next(self.responses)


def test_prompt_identical_across_reviewers(tmp_path):
    from c3.review import run_review

    first = FakeRunner(RunResult("[]", {}))
    second = FakeRunner(RunResult("[]", {}))
    pr = make_pr()

    run_review(first, pr, "claude", "diff", 1, tmp_path)
    run_review(second, pr, "gpt", "diff", 1, tmp_path)

    assert first.calls == second.calls


def test_agent_prompt_adds_only_agent_sentence():
    from c3.review import AGENT_SENTENCE, build_review_prompt

    diff_system, diff_user = build_review_prompt(make_pr(), "diff")
    agent_system, agent_user = build_review_prompt(make_pr(), "agent")
    assert agent_user == diff_user
    assert agent_system == diff_system + "\n" + AGENT_SENTENCE


def test_prompt_excludes_reference_and_pr_discussion_text():
    from c3.review import build_review_prompt

    pr = make_pr()
    prompt = "\n".join(build_review_prompt(pr, "agent"))
    assert pr.diff in prompt
    assert all(text not in prompt for text in (pr.title, pr.body, pr.references[0].text))


def test_runner_error_not_parsed(tmp_path):
    from c3.review import run_review

    runner = FakeRunner(RunResult("[]", {"usage": {"tokens": 32}}, "runner failed"))
    result = run_review(runner, make_pr(), "claude", "diff", 1, tmp_path)
    assert result.error == "runner failed"
    assert result.parse_error is False
    assert result.comments == []
    assert result.meta["usage"] == {"tokens": 32}
    assert len(runner.calls) == 1


@pytest.mark.parametrize("bad", [
    "garbage", "{}", '[{"file": "Main.java", "line": true, "comment": "text", "category": "testing"}]',
    '[{"file": "Main.java", "line": 1, "comment": "text"}]',
    '[{"file": "Main.java", "line": 1, "comment": "text", "category": "testing", "extra": 1}]',
])
def test_retry_then_parse_error(tmp_path, bad):
    from c3.review import run_review

    runner = FakeRunner(*(RunResult(bad, {"payload": index}) for index in range(3)))
    result = run_review(runner, make_pr(), "claude", "diff", 1, tmp_path)
    assert len(runner.calls) == 3
    assert result.parse_error is True
    assert result.error is None
    assert result.comments == []
    assert [attempt["text"] for attempt in result.meta["attempts"]] == [bad] * 3


def test_caches_distinct_attempt_and_run_identities(tmp_path):
    from c3.review import run_review

    workdir = tmp_path / "empty"
    workdir.mkdir()
    runner = FakeRunner(RunResult("{}", {}), RunResult("[]", {}), RunResult("[]", {}))
    wrapped = CachedRunner(runner, tmp_path / "cache", "review")
    pr = make_pr()

    assert not run_review(wrapped, pr, "claude", "diff", 1, workdir).parse_error
    assert not run_review(wrapped, pr, "claude", "diff", 2, workdir).parse_error
    identities = [
        json.loads(path.read_text())["identity"]
        for path in (tmp_path / "cache").glob("*.json")
    ]
    assert {(entry["run_idx"], entry["attempt_idx"]) for entry in identities} == {
        (1, 0), (1, 1), (2, 0),
    }
    assert len(runner.calls) == 3


def test_unknown_category_maps_to_design_and_counts(tmp_path):
    from c3.review import run_review

    payload = [{"file": "Main.java", "line": 22, "comment": "Issue", "category": "other"}]
    runner = FakeRunner(RunResult(json.dumps(payload), {}))
    result = run_review(runner, make_pr(), "claude", "agent", 1, tmp_path)
    assert runner.calls[0][2:] == (tmp_path, True)
    assert result.comments == [AiComment("Main.java", 22, "Issue", "design_maintainability")]
    assert result.meta["unknown_category_count"] == 1
    assert result.error is None and not result.parse_error
