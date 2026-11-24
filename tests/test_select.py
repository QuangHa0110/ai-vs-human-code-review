from dataclasses import replace

import pytest

from c3.models import PullRequest, Reference
from c3.select import anonymize, build_candidate, sample_prs

REPO = "org/example"
CUTOFF = "2026-01-01"
COMMENT = "This test misses a null value and can cause an exception in production."


def review_comment(number, login="reviewer-one", sha="reviewed", *, bot=False):
    return {
        "id": number,
        "user": {"login": login, "type": "Bot" if bot else "User"},
        "body": COMMENT,
        "created_at": "2026-01-05T00:00:00Z",
        "path": "Sample.java",
        "side": "RIGHT",
        "line": 1,
        "original_line": 1,
        "original_commit_id": sha,
        "commit_id": "final-head",
    }


def make_diff(lines, extension="java"):
    path = f"Sample.{extension}"
    return (
        f"diff --git a/{path} b/{path}\n--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1,{lines} @@\n"
        + "+value\n" * lines
    )


class GitHub:
    def __init__(self):
        self.pr = {
            "created_at": "2026-01-02T00:00:00Z",
            "merged": True,
            "merge_commit_sha": "merged-commit",
            "base": {"sha": "moving-base"},
            "head": {"sha": "final-head"},
            "title": "Changes by Reviewer-One",
            "body": "Thanks @reviewer-one and https://github.com/reviewer-two",
            "user": {"login": "author"},
        }
        self.comments = [
            review_comment(1),
            review_comment(2, "reviewer-two"),
            review_comment(3, sha="final-head"),
        ]
        self.diff = make_diff(500) + make_diff(600, "py")

    def get_pr(self, repo, number):
        return self.pr

    def review_comments(self, repo, number):
        return self.comments

    def first_parent(self, repo, sha):
        assert sha == "merged-commit"
        return "historical-target"

    def merge_base(self, repo, base, head):
        assert (base, head) == ("historical-target", "reviewed")
        return "shared-base"

    def compare_diff(self, repo, base, head):
        assert (base, head) == ("shared-base", "reviewed")
        return self.diff


def test_candidate_stores_first_review_comparison_and_java_only():
    github = GitHub()
    pr = build_candidate(github, REPO, 1, CUTOFF)
    assert pr is not None
    assert (pr.pr_id, pr.dataset) == ("org/example#1", "D2")
    assert (pr.base_sha, pr.review_sha) == ("shared-base", "reviewed")
    assert pr.diff == make_diff(500)
    assert [ref.ref_id for ref in pr.references] == ["org/example#1:1", "org/example#1:2"]
    assert all(ref.actionable is None for ref in pr.references)
    sanitized, mapping = anonymize([pr], "test-salt")
    assert set(mapping) == {"reviewer-one", "reviewer-two"}
    assert sanitized[0].title == f"Changes by {mapping['reviewer-one']}"


@pytest.mark.parametrize("created_at,merged", [
    ("2025-12-31T12:00:00Z", True),
    ("2026-01-01T00:00:00Z", True),
    ("2026-01-02T00:00:00Z", False),
])
def test_candidate_rejects_outside_merged_cutoff(created_at, merged):
    github = GitHub()
    github.pr.update(created_at=created_at, merged=merged)
    assert build_candidate(github, REPO, 1, CUTOFF) is None


@pytest.mark.parametrize("diff", [make_diff(501), make_diff(1, "py"), ""])
def test_candidate_rejects_java_diff_outside_size_limit(diff):
    github = GitHub()
    github.diff = diff
    assert build_candidate(github, REPO, 1, CUTOFF) is None


def test_candidate_fails_when_review_commit_is_already_in_historical_target():
    github = GitHub()
    github.merge_base = lambda *args: "reviewed"
    with pytest.raises(ValueError, match="historical"):
        build_candidate(github, REPO, 1, CUTOFF)


def test_candidate_rejects_excess_bot_comments():
    github = GitHub()
    github.comments += [review_comment(n, "gh-bot", bot=True) for n in (4, 5)]
    assert build_candidate(github, REPO, 1, CUTOFF) is None


def test_candidate_accepts_bot_ratio_equal_to_limit():
    github = GitHub()
    github.comments += [review_comment(n, "gh-bot", bot=True) for n in (4, 5, 6)]
    github.comments += [review_comment(n) for n in (7, 8, 9, 10)]
    assert build_candidate(github, REPO, 1, CUTOFF) is not None


def test_candidate_rejects_fewer_than_two_retained_references():
    github = GitHub()
    github.comments.pop(1)
    assert build_candidate(github, REPO, 1, CUTOFF) is None


def pr_record(repo=REPO, number=1):
    pr_id = f"{repo}#{number}"
    return PullRequest(
        pr_id, "D2", repo, number, "Discuss @Other-Reviewer",
        "Thanks @Sample-Review and sample-review in https://github.com/sample-review. "
        "See https://github.com/org/example and @org/team.",
        "base", "head", "diff",
        [Reference(f"{pr_id}:1", "Sample.java", 1, "Sample-Review reviewed it.", "human", True)],
    )


def test_sample_is_balanced_and_reproducible_under_input_reordering():
    candidates = [pr_record(repo, n) for repo, count in (("org/z", 6), ("org/a", 3), ("org/b", 1))
                  for n in range(1, count + 1)]
    picked = sample_prs(candidates, 2, 37)
    assert picked == sample_prs(list(reversed(candidates)), 2, 37)
    assert [p.repo for p in picked] == ["org/a", "org/a", "org/b", "org/z", "org/z"]
    assert [p.pr_id for p in picked] == sorted(p.pr_id for p in picked)


def test_anonymize_replaces_logins_consistently_without_mutating_inputs():
    original = pr_record()
    duplicate = replace(original, number=2, pr_id=f"{REPO}#2")
    result, mapping = anonymize([original, duplicate], "salt")
    assert mapping == anonymize([duplicate, original], "salt")[1]
    assert mapping != anonymize([original], "different-salt")[1]
    assert set(mapping) == {"sample-review", "other-reviewer"}
    handle = mapping["sample-review"]
    assert len(handle) == 17 and handle.startswith("reviewer_")
    int(handle[9:], 16)
    for pr in result:
        assert pr.body == (
            f"Thanks @{handle} and {handle} in https://github.com/{handle}. "
            "See https://github.com/org/example and @org/team."
        )
        assert pr.references[0].text == f"{handle} reviewed it."
        assert pr.title == f"Discuss @{mapping['other-reviewer']}"
    assert "@Sample-Review" in original.body
    assert original.references[0].text == "Sample-Review reviewed it."


def test_anonymize_replaces_markdown_emphasized_mentions_and_profile_links():
    original = pr_record()
    original._reviewer_logins = {"sample-review"}
    original.body = (
        "_@sample-review_ **@Other-Reviewer** "
        "https://github.com/sample-review and https://github.com/org/example"
    )
    sanitized, mapping = anonymize([original], "salt")
    assert sanitized[0].body == (
        f"_@{mapping['sample-review']}_ **@{mapping['other-reviewer']}** "
        f"https://github.com/{mapping['sample-review']} and https://github.com/org/example"
    )


def test_anonymize_replaces_bare_logins_in_markdown_emphasis():
    original = pr_record()
    original._reviewer_logins = {"sample-review", "reviewer"}
    original.body = "_sample-review_ __sample-review__ foo-sample-review @sample-review"
    sanitized, mapping = anonymize([original], "salt")
    alias = mapping["sample-review"]
    assert sanitized[0].body == f"_{alias}_ __{alias}__ foo-sample-review @{alias}"
