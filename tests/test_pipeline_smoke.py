from c3.cli import main
from c3.config import Settings
from c3.models import Reference

from test_report import _pr, _rows, _write_run


def test_analyze_smoke_handles_failed_reviews_and_unpaired_context(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "data", results_dir=tmp_path / "results")
    for dataset, count in (("D1", 2), ("D2", 3)):
        for number in range(1, count + 1):
            pr = _pr(dataset, number)
            if dataset == "D1":
                pr.references.append(Reference("n1", "A.java", 2, "expert negative", "human", False))
            for reviewer in ("claude", "gpt"):
                _write_run(settings, pr, reviewer, hit=(reviewer == "claude"))
                if reviewer == "claude" and number == 1:
                    _write_run(settings, pr, reviewer, mode="agent", hit=True)
            if dataset == "D2":
                _write_run(settings, pr, "deepseek", error="provider unavailable")
    monkeypatch.setenv("C3_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("C3_RESULTS_DIR", str(settings.results_dir))

    assert main(["analyze"]) == 0

    rq1_rows = _rows(settings.results_dir / "rq1_rq2.csv")
    assert any(row["dataset"] == "D2" and row["reviewer"] == "deepseek"
               and row["metric"] == "n_run_error" and row["value"] == "3" for row in rq1_rows)
    claude = next(row for row in rq1_rows if row["dataset"] == "D2" and row["reviewer"] == "claude"
                  and row["mode"] == "diff" and row["metric"] == "recall_all")
    gpt = next(row for row in rq1_rows if row["dataset"] == "D2" and row["reviewer"] == "gpt"
               and row["mode"] == "diff" and row["metric"] == "recall_all")
    assert float(claude["value"]) > float(gpt["value"])
    context_rows = _rows(settings.results_dir / "rq6_context.csv")
    assert any(row["reviewer"] == "claude" and row["dataset"] == "D2"
               and row["status"] == "missing_agent_pair" for row in context_rows)
    assert (settings.results_dir / "rq1_recall.png").is_file()

