import json

from c3 import cli
from c3.config import ReviewerSpec
from c3.models import PullRequest, Reference, save_json
from c3.runners import RunResult


def make_pr(dataset="D1", number=1, sha="b" * 40):
    return PullRequest(f"org/example#{number}", dataset, "org/example", number,
                       "Title", "Body", "a" * 40, sha,
                       "diff --git a/Main.java b/Main.java\n+class Main {}\n", [])


class FakeRunner:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.spec = ReviewerSpec("claude", "claude_cli", "fixture-model")
        self.tool_version = "fixture-version"

    def run(self, system, user, workdir, allow_read_tools):
        self.calls.append((system, user, workdir, allow_read_tools))
        if not self.responses:
            return RunResult("[]", {})
        value = self.responses.pop(0)
        return value


def write_pr(root, pr):
    path = root / "prs" / pr.dataset / f"{pr.pr_id.replace('/', '__')}.json"
    save_json(pr, path)
    save_json({"dataset": pr.dataset, "evaluation": {"prs": 1},
               "selected_pr_ids": [pr.pr_id]}, path.parent / "_meta.json")


def invoke(dataset="D1"):
    return cli.main(["review", "--reviewer", "claude", "--mode", "diff",
                     "--runs", "1", "--dataset", dataset])


def test_review_cli_fresh_run_and_resume_skips_matching_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("C3_MODEL_CLAUDE", "fixture-model")
    write_pr(tmp_path, make_pr())
    runner = FakeRunner(RunResult('[{"file":"Main.java","line":1,"comment":"issue","category":"testing"}]', {}))
    monkeypatch.setattr(cli, "make_runner", lambda *args: runner)

    assert invoke() == 0
    assert invoke() == 0
    assert len(runner.calls) == 1
    output = tmp_path / "reviews/D1/claude/diff/run1/org__example#1.json"
    record = json.loads(output.read_text())
    assert record["comments"][0]["comment"] == "issue"
    assert record["fingerprint"]


def test_review_cli_changed_input_refuses_to_reuse_saved_result(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("C3_MODEL_CLAUDE", "fixture-model")
    write_pr(tmp_path, make_pr())
    runner = FakeRunner(RunResult("[]", {}))
    monkeypatch.setattr(cli, "make_runner", lambda *args: runner)
    assert invoke() == 0

    write_pr(tmp_path, make_pr(sha="c" * 40))
    assert invoke() == 1
    assert "fingerprint" in capsys.readouterr().err
    assert len(runner.calls) == 1


def test_review_cli_reruns_structurally_invalid_saved_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("C3_MODEL_CLAUDE", "fixture-model")
    write_pr(tmp_path, make_pr())
    runner = FakeRunner(RunResult("[]", {}), RunResult("[]", {}))
    monkeypatch.setattr(cli, "make_runner", lambda *args: runner)
    assert invoke() == 0

    artifact = tmp_path / "reviews/D1/claude/diff/run1/org__example#1.json"
    record = json.loads(artifact.read_text())
    record["comments"] = [{}]
    artifact.write_text(json.dumps(record))
    assert invoke() == 0
    assert len(runner.calls) == 2


def test_review_cli_rejects_malformed_existing_artifact(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("C3_MODEL_CLAUDE", "fixture-model")
    write_pr(tmp_path, make_pr())
    artifact = tmp_path / "reviews/D1/claude/diff/run1/org__example#1.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("[]")
    runner = FakeRunner(RunResult("[]", {}))
    monkeypatch.setattr(cli, "make_runner", lambda *args: runner)

    assert invoke() == 1
    assert "Invalid review artifact" in capsys.readouterr().err
    assert not runner.calls


def test_agent_checkouts_use_unique_destinations_for_same_pr_number(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("C3_MODEL_CLAUDE", "fixture-model")
    prs = [make_pr("D1", 1), make_pr("D1", 1)]
    prs[1].repo = "org/other"
    prs[1].pr_id = "org/other#1"
    for pr in prs:
        path = tmp_path / "prs/D1" / f"{pr.pr_id.replace('/', '__')}.json"
        save_json(pr, path)
    save_json({"dataset": "D1", "selected_pr_ids": [pr.pr_id for pr in prs]},
              tmp_path / "prs/D1/_meta.json")
    destinations = []

    def checkout(url, sha, dest):
        dest.mkdir()
        destinations.append(dest)
        return dest

    runner = FakeRunner(RunResult("[]", {}), RunResult("[]", {}))
    monkeypatch.setattr(cli, "prepare_checkout", checkout)
    monkeypatch.setattr(cli, "make_runner", lambda *args: runner)
    assert cli.main(["review", "--reviewer", "claude", "--mode", "agent",
                     "--runs", "1", "--dataset", "D1"]) == 0
    assert len(set(destinations)) == 2


def test_review_cli_requires_classified_d2_manifest(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("C3_MODEL_CLAUDE", "fixture-model")
    write_pr(tmp_path, make_pr("D2"))
    runner = FakeRunner(RunResult("[]", {}))
    monkeypatch.setattr(cli, "make_runner", lambda *args: runner)

    assert invoke("D2") == 1
    assert "classified/D2" in capsys.readouterr().err
    assert not runner.calls


def test_review_cli_requires_d2_retained_ids_in_classified_manifest(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("C3_MODEL_CLAUDE", "fixture-model")
    pr = make_pr("D2")
    write_pr(tmp_path, pr)
    classified = tmp_path / "classified/D2"
    save_json({"dataset": "D2", "selected_pr_ids": [pr.pr_id],
               "input_pr_count": 1, "retained_pr_count": 1, "error_count": 0},
              classified / "_meta.json")
    save_json(pr, classified / f"{pr.pr_id.replace('/', '__')}.json")
    runner = FakeRunner(RunResult("[]", {}))
    monkeypatch.setattr(cli, "make_runner", lambda *args: runner)

    assert invoke("D2") == 1
    assert "retained_pr_ids" in capsys.readouterr().err
    assert not runner.calls


def test_review_cli_retries_parse_failure_and_persists_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("C3_MODEL_CLAUDE", "fixture-model")
    write_pr(tmp_path, make_pr())
    runner = FakeRunner(RunResult("not json", {}), RunResult("[]", {}))
    monkeypatch.setattr(cli, "make_runner", lambda *args: runner)

    assert invoke() == 0
    assert len(runner.calls) == 2
    record = json.loads((tmp_path / "reviews/D1/claude/diff/run1/org__example#1.json").read_text())
    assert record["parse_error"] is False
    assert record["comments"] == []
