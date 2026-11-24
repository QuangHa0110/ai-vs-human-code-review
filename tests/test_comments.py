import json
from pathlib import Path

import pytest

from c3.comments import CleanResult, clean_comments, is_substantive
from c3.models import Reference


@pytest.fixture
def raw_comments():
    return json.loads((Path(__file__).parent / "fixtures" / "raw_comments.json").read_text(encoding="utf-8"))


@pytest.fixture
def result(raw_comments):
    return clean_comments(raw_comments, "fixture-author", "fixture/repo#1")


def test_bot_reply_author_dropped(result):
    assert result.dropped["bot"] == 2
    assert result.dropped["reply"] == 1
    assert result.dropped["author"] == 1


def test_not_java_and_left_side_dropped(result):
    assert result.dropped["not_java"] == 1
    assert result.dropped["left_side"] == 1


def test_null_line_counted_as_outdated(result):
    assert result.dropped["outdated"] == 1


def test_lgtm_not_substantive(result):
    assert not is_substantive("LGTM")
    assert result.dropped["not_substantive"] == 1


@pytest.mark.parametrize("indicator", ["`item`", "someValue", "run()", "NULL"])
def test_substantive_comment_requires_ten_words_and_code_or_keyword(indicator):
    assert is_substantive(f"One two three four five six seven eight nine {indicator}")
    assert not is_substantive(f"One two three four five six seven eight {indicator}")
    assert not is_substantive("One two three four five six seven eight nine ten")


def test_only_first_round_kept(raw_comments, result):
    assert result.review_sha == "review-revision"
    assert result.dropped["later_round"] == 2
    assert sum(result.dropped.values()) + len(result.kept) == len(raw_comments)
    assert result.kept == [
        Reference(
            ref_id=f"fixture/repo#1:{identifier}",
            path="src/Example.java",
            line=line,
            text="Please handle a null argument here before calling the formatter.",
            origin="human",
            valid=True,
        )
        for identifier, line in [(1, 12), (7, 16), (9, 18)]
    ]
    assert clean_comments(raw_comments[::-1], "fixture-author", "fixture/repo#1") == result


def test_bot_ratio_computed(raw_comments, result):
    assert result.bot_ratio == pytest.approx(2 / len(raw_comments))


def test_no_surviving_references(raw_comments):
    outdated = next(comment for comment in raw_comments if comment["line"] is None)
    assert clean_comments([outdated], "fixture-author", "fixture/repo#1") == CleanResult(
        kept=[], dropped={"outdated": 1}, review_sha=None, bot_ratio=0.0
    )
    assert clean_comments([], "fixture-author", "fixture/repo#1") == CleanResult(
        kept=[], dropped={}, review_sha=None, bot_ratio=0.0
    )


def test_current_line_fallback_requires_matching_revision(raw_comments):
    comment = next(comment for comment in raw_comments if comment["id"] == 1)
    missing_original_line = {**comment, "original_line": None}
    matching = {**missing_original_line, "commit_id": comment["original_commit_id"]}
    result = clean_comments([missing_original_line, matching], "fixture-author", "fixture/repo#1")

    assert result.dropped == {"outdated": 1}
    assert [reference.line for reference in result.kept] == [comment["line"]]


def test_missing_original_revision_is_outdated(raw_comments):
    comment = next(comment for comment in raw_comments if comment["id"] == 1)
    result = clean_comments([{**comment, "original_commit_id": None}], "fixture-author", "fixture/repo#1")
    assert result == CleanResult(kept=[], dropped={"outdated": 1}, review_sha=None, bot_ratio=0.0)
