import json

import httpx
import pytest

from c3 import cli
from c3.models import load_pr
from test_select import GitHub, make_diff


def run_collect():
    return cli.main([
        "collect", "--repos", "org/example,org/other", "--since", "2026-01-01",
        "--until", "2026-02-01", "--n-per-repo", "2",
    ])


def test_collect_writes_anonymized_sample_and_audit_counts(tmp_path, monkeypatch):
    example = GitHub()
    requests = []

    def respond(request):
        requests.append(request)
        path = request.url.path
        assert "Authorization" not in request.headers
        if path == "/search/issues":
            repo = request.url.params["q"].split(" ")[0]
            numbers = [1, 2] if repo == "repo:org/example" else []
            return httpx.Response(200, json={"items": [{"number": n} for n in numbers]})
        if path.endswith("/pulls/2"):
            return httpx.Response(200, json=example.pr | {"created_at": "2026-01-01T00:00:00Z"})
        if path.endswith("/pulls/1"):
            return httpx.Response(200, json=example.pr)
        if path.endswith("/pulls/1/comments"):
            return httpx.Response(200, json=example.comments)
        if path.endswith("/commits/merged-commit"):
            return httpx.Response(200, json={"parents": [{"sha": "historical-target"}, {"sha": "final-head"}]})
        if path.endswith("/compare/historical-target...reviewed"):
            assert request.headers["Accept"] == "application/vnd.github+json"
            return httpx.Response(200, json={"merge_base_commit": {"sha": "shared-base"}})
        if path.endswith("/compare/shared-base...reviewed"):
            assert request.headers["Accept"] == "application/vnd.github.diff"
            return httpx.Response(200, text=example.diff)
        raise AssertionError(f"Unexpected request: {request.url}")

    http = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr("c3.github_client.httpx.Client", lambda **kwargs: http)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("C3_SEED", "37")
    monkeypatch.setenv("C3_ANONYMIZATION_SALT", "test-secret")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    assert run_collect() == 0
    assert http.is_closed
    output = tmp_path / "prs" / "D2"
    assert {file.name for file in output.iterdir()} == {"org__example#1.json", "_meta.json"}
    pr = load_pr(output / "org__example#1.json")
    assert (pr.pr_id, pr.diff, pr.review_sha) == ("org/example#1", make_diff(500), "reviewed")
    assert "reviewer-one" not in pr.body and "reviewer-two" not in pr.body
    meta = json.loads((output / "_meta.json").read_text())
    assert meta["seed"] == 37
    assert meta["n_per_repo"] == 2
    assert meta["reject_counts"] == {"created_at_cutoff": 1}
    assert meta["shortages"] == {"org/example": 1, "org/other": 2}
    assert meta["selected_pr_ids"] == ["org/example#1"]
    assert "test-secret" not in (output / "_meta.json").read_text()
    assert requests


def test_collect_requires_salt_before_network_or_writes(tmp_path, monkeypatch, capsys):
    def unexpected(**kwargs):
        raise AssertionError("GitHub client should not be initialized without salt")

    monkeypatch.setattr(cli, "GitHubClient", unexpected)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("C3_ANONYMIZATION_SALT", raising=False)
    assert run_collect() != 0
    assert "C3_ANONYMIZATION_SALT" in capsys.readouterr().err
    assert not (tmp_path / "prs").exists()


def test_collect_aborts_without_publishing_on_api_error(tmp_path, monkeypatch, capsys):
    http = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    monkeypatch.setattr("c3.github_client.httpx.Client", lambda **kwargs: http)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("C3_ANONYMIZATION_SALT", "test-secret")
    assert run_collect() != 0
    assert "GitHub" in capsys.readouterr().err
    assert http.is_closed
    assert not (tmp_path / "prs").exists()


def test_collect_preserves_existing_collection_with_different_parameters(tmp_path, monkeypatch, capsys):
    output = tmp_path / "prs" / "D2"
    output.mkdir(parents=True)
    metadata = output / "_meta.json"
    metadata.write_text('{"n_per_repo": 7, "selected_pr_ids": ["org/example#1"]}\n')
    original = output / "org__example#1.json"
    original.write_text("existing PR data")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("C3_ANONYMIZATION_SALT", "test-secret")
    http = httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"items": []}),
    ))
    monkeypatch.setattr("c3.github_client.httpx.Client", lambda **kwargs: http)

    assert run_collect() != 0
    assert "C3_DATA_DIR" in capsys.readouterr().err
    assert json.loads(metadata.read_text())["n_per_repo"] == 7
    assert original.read_text() == "existing PR data"


@pytest.fixture
def empty_collection(tmp_path, monkeypatch):
    make_client = httpx.Client
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"items": []}))
    monkeypatch.setattr("c3.github_client.httpx.Client", lambda **kwargs: make_client(transport=transport))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("C3_ANONYMIZATION_SALT", "test-secret")
    return tmp_path / "prs" / "D2"


@pytest.mark.parametrize("with_manifest", [False, True])
def test_collect_rejects_json_orphans_even_for_an_empty_sample(empty_collection, capsys, with_manifest):
    output = empty_collection
    if with_manifest:
        assert run_collect() == 0
    output.mkdir(parents=True, exist_ok=True)
    orphan = output / "org__example#99.json"
    orphan.write_text("existing PR data")

    assert run_collect() != 0
    assert "C3_DATA_DIR" in capsys.readouterr().err
    assert orphan.read_text() == "existing PR data"
    assert (output / "_meta.json").exists() == with_manifest


def test_collect_requires_the_same_salt_to_resume(empty_collection, monkeypatch, capsys):
    assert run_collect() == 0
    assert run_collect() == 0
    manifest = empty_collection / "_meta.json"
    original_metadata = manifest.read_text()
    monkeypatch.setenv("C3_ANONYMIZATION_SALT", "different-test-secret")

    assert run_collect() != 0
    assert "C3_ANONYMIZATION_SALT" in capsys.readouterr().err
    assert manifest.read_text() == original_metadata


@pytest.fixture
def recorded_collection(empty_collection, monkeypatch):
    class GitHubSample(GitHub):
        def search_merged_prs(self, repo, since, until):
            return [{"number": 1}, {"number": 2}] if repo == "org/example" else []

        def close(self):
            pass

    github = GitHubSample()
    monkeypatch.setattr(cli, "GitHubClient", lambda _: github)
    assert run_collect() == 0
    return empty_collection, github


def test_collect_rejects_malformed_saved_pr(recorded_collection, capsys):
    output, _ = recorded_collection
    record = output / "org__example#1.json"
    record.write_text("{")
    assert run_collect() != 0
    assert "C3_DATA_DIR" in capsys.readouterr().err
    assert record.read_text() == "{"


def test_collect_rejects_drift_from_a_complete_snapshot(recorded_collection, capsys):
    output, github = recorded_collection
    record = output / "org__example#1.json"
    prior = record.read_bytes()
    assert run_collect() == 0
    github.pr["body"] = "Description edited since initial collection"
    assert run_collect() != 0
    assert "C3_DATA_DIR" in capsys.readouterr().err
    assert record.read_bytes() == prior


def test_collect_rejects_incomplete_snapshot_even_if_remaining_records_match(recorded_collection, capsys):
    output, _ = recorded_collection
    missing = output / "org__example#2.json"
    missing.unlink()
    assert run_collect() != 0
    assert "C3_DATA_DIR" in capsys.readouterr().err
    assert not missing.exists()
