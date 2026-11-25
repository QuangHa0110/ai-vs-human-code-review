import hashlib
import json

from c3 import cli
from c3.models import AiComment, PullRequest, Reference, save_json
from c3.runners import RunResult


def make_pr(number=1, text="human"):
    return PullRequest(f"org/example#{number}", "D1", "org/example", number,
                       "title", "body", "a" * 40, "b" * 40, f"diff {number}",
                       [Reference("ref1", "a.py", 1, text, "human", True)])


class FakeRunner:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def run(self, system, user, workdir, allow_read_tools):
        self.calls.append((system, user, workdir, allow_read_tools))
        return self.responses.pop(0)


def write_dataset(tmp_path, prs, classified=False):
    directory = tmp_path / ("classified" if classified else "prs") / "D1"
    for pr in prs:
        save_json(pr, directory / f"{pr.pr_id.replace('/', '__')}.json")
    save_json({"dataset": "D1", "selected_pr_ids": [pr.pr_id for pr in prs]},
              directory / "_meta.json")


def write_review(tmp_path, pr, *, comments=None, error=None, parse_error=False, source=None, **changes):
    identity = {"dataset": pr.dataset, "pr_id": pr.pr_id, "repo": pr.repo,
                "base_sha": pr.base_sha, "head_sha": pr.review_sha, "diff": pr.diff,
                "mode": "diff", "reviewer": "reviewer", "run_idx": 1}
    identity.update(changes)
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    record = {"pr_id": pr.pr_id, "reviewer": "reviewer", "mode": "diff", "run_idx": 1,
              "comments": [comment.__dict__ for comment in (comments if comments is not None else
                                                               [AiComment("a.py", 1, "AI", "testing")])],
              "meta": {}, "error": error, "parse_error": parse_error,
              "fingerprint": fingerprint, "fingerprint_input": identity,
              "source": str(source or tmp_path / "prs/D1")}
    path = tmp_path / "reviews/D1/reviewer/diff/run1" / f"{pr.pr_id.replace('/', '__')}.json"
    save_json(record, path)
    return path


def invoke(*extra):
    return cli.main(["judge", "--judge", "claude", "--dataset", "D1", "--reviewer", "reviewer",
                     "--mode", "diff", "--run", "1", *extra])


def setup(tmp_path, monkeypatch, prs, runner, *, classified=False):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("C3_MODEL_CLAUDE", "fixture-model")
    write_dataset(tmp_path, prs, classified)
    for pr in prs:
        write_review(tmp_path, pr, source=tmp_path / ("classified" if classified else "prs") / "D1")
    monkeypatch.setattr(cli, "make_runner", lambda *args: runner)


def test_judge_cli_saves_atomic_shape_and_resumes_matching_judgment(tmp_path, monkeypatch):
    pr = make_pr()
    runner = FakeRunner(RunResult('[["ref1",0]]', {}))
    setup(tmp_path, monkeypatch, [pr], runner)

    assert invoke() == 0
    assert invoke() == 0
    assert len(runner.calls) == 1
    assert runner.calls[0][3] is False
    artifact = tmp_path / "judgments/D1/claude/reviewer/diff/run1/org__example#1.json"
    record = json.loads(artifact.read_text())
    assert record["matches"] == [{"ref_id": "ref1", "ai_index": 0}]
    assert record["fingerprint"]


def test_judge_cli_uses_classified_pr_source(tmp_path, monkeypatch):
    raw, classified = make_pr(text="raw"), make_pr(text="classified")
    runner = FakeRunner(RunResult("[]", {}))
    setup(tmp_path, monkeypatch, [raw], runner)
    write_dataset(tmp_path, [classified], classified=True)
    write_review(tmp_path, classified, source=tmp_path / "classified/D1")

    assert invoke() == 0
    assert "classified" in runner.calls[0][1]
    assert "raw" not in runner.calls[0][1]


def test_judge_cli_rejects_review_artifact_for_changed_pr(tmp_path, monkeypatch, capsys):
    pr = make_pr()
    runner = FakeRunner(RunResult("[]", {}))
    setup(tmp_path, monkeypatch, [pr], runner)
    changed = make_pr()
    changed.diff = "changed"
    write_dataset(tmp_path, [changed])

    assert invoke() == 1
    assert "Review fingerprint" in capsys.readouterr().err
    assert not runner.calls


def test_judge_cli_records_failed_review_skip_count(tmp_path, monkeypatch):
    pr = make_pr()
    runner = FakeRunner()
    setup(tmp_path, monkeypatch, [pr], runner)
    write_review(tmp_path, pr, error="review failed")

    assert invoke() == 0
    assert not runner.calls
    summary = json.loads((tmp_path / "judgments/D1/claude/reviewer/diff/run1/_meta.json").read_text())
    assert summary["skipped_run_error"] == 1


def test_judge_cli_refuses_to_skip_review_failure_when_judgment_already_exists(
        tmp_path, monkeypatch, capsys):
    pr = make_pr()
    runner = FakeRunner(RunResult('[["ref1",0]]', {}))
    setup(tmp_path, monkeypatch, [pr], runner)
    assert invoke() == 0
    judgment = tmp_path / "judgments/D1/claude/reviewer/diff/run1/org__example#1.json"
    saved_judgment = judgment.read_bytes()

    write_review(tmp_path, pr, error="review failed")
    assert invoke() == 1

    assert "existing judgment" in capsys.readouterr().err
    assert judgment.read_bytes() == saved_judgment
    assert len(runner.calls) == 1


def test_judge_cli_rejects_corrupt_existing_judgment_payload(tmp_path, monkeypatch, capsys):
    pr = make_pr()
    runner = FakeRunner(RunResult('[["ref1",0]]', {}))
    setup(tmp_path, monkeypatch, [pr], runner)
    assert invoke() == 0
    judgment = tmp_path / "judgments/D1/claude/reviewer/diff/run1/org__example#1.json"
    record = json.loads(judgment.read_text())
    record["matches"] = [{}]
    judgment.write_text(json.dumps(record))

    assert invoke() == 1

    assert "Invalid existing judgment artifact" in capsys.readouterr().err
    assert len(runner.calls) == 1


def test_judge_cli_rejects_existing_judgment_fingerprint_input_change(tmp_path, monkeypatch, capsys):
    pr = make_pr()
    runner = FakeRunner(RunResult('[["ref1",0]]', {}))
    setup(tmp_path, monkeypatch, [pr], runner)
    assert invoke() == 0
    judgment = tmp_path / "judgments/D1/claude/reviewer/diff/run1/org__example#1.json"
    record = json.loads(judgment.read_text())
    record["fingerprint_input"]["model"] = "different-model"
    judgment.write_text(json.dumps(record))

    assert invoke() == 1

    assert "Invalid existing judgment artifact" in capsys.readouterr().err
    assert len(runner.calls) == 1


def test_judge_cli_rejects_summary_identity_change_retaining_fingerprint(tmp_path, monkeypatch, capsys):
    pr = make_pr()
    runner = FakeRunner(RunResult('[["ref1",0]]', {}))
    setup(tmp_path, monkeypatch, [pr], runner)
    assert invoke() == 0
    summary = tmp_path / "judgments/D1/claude/reviewer/diff/run1/_meta.json"
    record = json.loads(summary.read_text())
    record["selected_pr_ids"] = []
    summary.write_text(json.dumps(record))

    assert invoke() == 1

    assert "Judgment fingerprint mismatch" in capsys.readouterr().err
    assert len(runner.calls) == 1


def test_judge_cli_failure_aborts_without_empty_judgment(tmp_path, monkeypatch, capsys):
    pr = make_pr()
    runner = FakeRunner(RunResult("[]", {}, "private runner error"))
    setup(tmp_path, monkeypatch, [pr], runner)

    assert invoke() == 1
    assert "judge runner failed" in capsys.readouterr().err
    assert not (tmp_path / "judgments/D1/claude/reviewer/diff/run1/org__example#1.json").exists()
