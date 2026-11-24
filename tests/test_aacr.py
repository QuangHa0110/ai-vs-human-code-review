import json
import os
from hashlib import sha256
from pathlib import Path

import httpx
import pytest

from c3 import cli
from c3.models import load_pr

FIXTURES = Path(__file__).parent / "fixtures"
POSITIVE = FIXTURES / "aacr_mini_positive.json"
NEGATIVE = FIXTURES / "aacr_mini_negative.json"
JAVA_DIFF = "diff --git a/Example.java b/Example.java\n--- a/Example.java\n+++ b/Example.java\n@@ -7 +7 @@\n-old\n+new\n"
OTHER_DIFF = "diff --git a/build.gradle b/build.gradle\n--- a/build.gradle\n+++ b/build.gradle\n@@ -7 +7 @@\n-old\n+new\n"


def test_load_filters_language():
    from c3.aacr import load_aacr

    assert [pr.pr_id for pr in load_aacr(POSITIVE, NEGATIVE)] == ["acme/example#8", "acme/other#9"]
    python, = load_aacr(POSITIVE, NEGATIVE, "Python")
    assert [ref.path for ref in python.references] == ["example.py"]


def test_positive_valid_negative_invalid():
    from c3.aacr import load_aacr

    pr = load_aacr(POSITIVE, NEGATIVE)[0]
    assert [(ref.ref_id, ref.valid) for ref in pr.references] == [
        ("acme/example#8:p0", True), ("acme/example#8:n0", False), ("acme/example#8:n1", False),
    ]
    ref = pr.references[0]
    assert (ref.path, ref.line, ref.text, ref.category) == (
        "Example.java", 7, "Guard against null before taking the length.", "Code Defect",
    )
    assert (pr.base_sha, pr.review_sha) == ("1" * 40, "2" * 40)
    assert (pr.diff, pr.title, pr.body) == ("", "", "")


def test_origin_from_is_ai_comment():
    from c3.aacr import load_aacr

    pr, other = load_aacr(POSITIVE, NEGATIVE)
    assert [ref.origin for ref in pr.references] == ["human", "ai", "human"]
    assert other.references[0].origin == "ai"


def test_summary_counts():
    from c3.aacr import load_aacr_with_audit, summary

    prs, audit = load_aacr_with_audit(POSITIVE, NEGATIVE)
    assert summary(prs) == {
        "prs": 2, "repos": 2, "prs_by_repo": {"acme/example": 1, "acme/other": 1},
        "references": {"positive": {"human": 1, "ai": 1}, "negative": {"human": 1, "ai": 1}},
    }
    assert audit["raw"]["references"] == {
        "positive": {"human": 2, "ai": 3}, "negative": {"human": 1, "ai": 1},
    }
    assert audit["dropped_references"] == {"left_side": 1, "not_java": 1, "missing_line": 1}


def test_load_rejects_inconsistent_commits(tmp_path):
    from c3.aacr import load_aacr

    negative = tmp_path / "negative.json"
    rows = json.loads(NEGATIVE.read_bytes())
    rows[0]["target_commit"] = "9" * 40
    negative.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match="inconsistent.*commit"):
        load_aacr(POSITIVE, negative)


def test_load_rejects_identical_inputs_at_different_paths(tmp_path):
    from c3.aacr import load_aacr

    duplicate = tmp_path / "negative.json"
    duplicate.write_bytes(POSITIVE.read_bytes())
    with pytest.raises(ValueError, match="identical"):
        load_aacr(POSITIVE, duplicate)


@pytest.mark.parametrize("wrong_slot", ["positive", "negative"])
def test_load_rejects_a_pinned_release_in_wrong_slot(monkeypatch, wrong_slot):
    from c3 import aacr

    pinned_role = "negative" if wrong_slot == "positive" else "positive"
    pinned_file = POSITIVE if pinned_role == "positive" else NEGATIVE
    monkeypatch.setitem(aacr._PINNED_SHA256, pinned_role, sha256(pinned_file.read_bytes()).hexdigest())
    positive, negative = (NEGATIVE, POSITIVE)

    with pytest.raises(ValueError, match=f"pinned {pinned_role}.*--{wrong_slot}"):
        aacr.load_aacr(positive, negative)


@pytest.mark.parametrize("field,value", [
    ("githubPrUrl", "https://example.net/owner/repo/pull/1"),
    ("source_commit", "main"), ("is_ai_comment", "false"), ("side", "unknown"),
    ("to_line", True), ("note", None), ("path", 42),
])
def test_load_rejects_invalid_schema(tmp_path, field, value):
    from c3.aacr import load_aacr

    negative = tmp_path / "negative.json"
    rows = json.loads(NEGATIVE.read_bytes())
    target = rows[0] if field in rows[0] else rows[0]["comments"][0]
    target[field] = value
    negative.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match=field):
        load_aacr(POSITIVE, negative)


def run_load_aacr():
    return cli.main(["load-aacr", "--positive", str(POSITIVE), "--negative", str(NEGATIVE)])


@pytest.fixture
def aacr_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("C3_ANONYMIZATION_SALT", "test-salt")
    return tmp_path / "prs" / "D1"


def test_load_aacr_command_writes_java_diffs_and_audit(aacr_output, monkeypatch):
    paths = []

    def respond(request):
        paths.append(request.url.path)
        assert request.headers["Accept"] == "application/vnd.github.diff"
        return httpx.Response(200, text=JAVA_DIFF + OTHER_DIFF)

    http = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr("c3.github_client.httpx.Client", lambda **kwargs: http)
    assert run_load_aacr() == 0
    assert paths == [
        f"/repos/acme/example/compare/{'1' * 40}...{'2' * 40}",
        f"/repos/acme/other/compare/{'3' * 40}...{'4' * 40}",
    ]
    assert http.is_closed
    assert {file.name for file in aacr_output.iterdir()} == {
        "acme__example#8.json", "acme__other#9.json", "_meta.json",
    }
    pr = load_pr(aacr_output / "acme__example#8.json")
    assert pr.diff == JAVA_DIFF
    assert "sample-reviewer" not in pr.references[1].text
    assert "@reviewer_" in pr.references[1].text
    meta = json.loads((aacr_output / "_meta.json").read_bytes())
    assert len(meta["inputs"]["positive"]["sha256"]) == 64
    assert meta["inputs"]["positive"]["name"] == POSITIVE.name
    assert meta["raw"]["prs"] == 2
    assert meta["evaluation"]["references"]["positive"] == {"human": 1, "ai": 1}


@pytest.mark.parametrize("response", [httpx.Response(503), httpx.Response(200, text=OTHER_DIFF)])
def test_load_aacr_does_not_publish_when_diff_unavailable(aacr_output, monkeypatch, response):
    http = httpx.Client(transport=httpx.MockTransport(lambda _: response))
    monkeypatch.setattr("c3.github_client.httpx.Client", lambda **kwargs: http)
    assert run_load_aacr() == 1
    assert http.is_closed
    assert not aacr_output.exists()


def test_load_aacr_preserves_nonempty_output(aacr_output, monkeypatch):
    aacr_output.mkdir(parents=True)
    existing = aacr_output / "existing.json"
    existing.write_text("previous data")

    def unexpected(*args, **kwargs):
        raise AssertionError("Existing output should be checked before accessing GitHub")

    monkeypatch.setattr(cli, "GitHubClient", unexpected)
    assert run_load_aacr() == 1
    assert existing.read_text() == "previous data"


def test_load_aacr_requires_salt(aacr_output, monkeypatch, capsys):
    monkeypatch.delenv("C3_ANONYMIZATION_SALT")

    def unexpected(*args, **kwargs):
        raise AssertionError("Salt should be checked before accessing GitHub")

    monkeypatch.setattr(cli, "GitHubClient", unexpected)
    assert run_load_aacr() == 1
    assert "C3_ANONYMIZATION_SALT" in capsys.readouterr().err
    assert not aacr_output.exists()


@pytest.mark.data
def test_real_java_counts():
    from c3.aacr import load_aacr_with_audit, summary

    root = Path(os.environ.get("C3_AACR_V1_DIR", "data/raw/aacr-bench/v1"))
    positive = root / "positive_samples.json"
    negative = root / "negative_samples.json"
    if not positive.is_file() or not negative.is_file():
        pytest.skip("Set C3_AACR_V1_DIR to the pinned AACR-Bench v1 data directory")

    prs, audit = load_aacr_with_audit(positive, negative)
    assert audit["upstream_revision"] == "07ba1b35102110404f6a931bf05dea33e6d89389"
    assert audit["raw"]["prs"] == len(prs) == 30
    assert audit["raw"]["split_prs"] == {"positive": 30, "negative": 18}
    assert audit["raw"]["references"] == {
        "positive": {"human": 61, "ai": 152}, "negative": {"human": 16, "ai": 43},
    }
    assert summary(prs)["references"] == {
        "positive": {"human": 56, "ai": 146}, "negative": {"human": 15, "ai": 42},
    }
    assert audit["dropped_references"] == {"not_java": 12, "left_side": 1}
