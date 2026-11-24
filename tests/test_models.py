import json
from pathlib import Path

from c3.models import AACR_CATEGORY_MAP, CATEGORIES, PullRequest, Reference, load_pr, save_json


def test_pr_roundtrip(tmp_path):
    pr = PullRequest("owner/repo#1", "D1", "owner/repo", 1, "Title", "Nội dung", "base", "review", "diff", [Reference("r1", "A.java", None, "Issue", "human", True)])
    path = tmp_path / "nested" / "pr.json"
    save_json(pr, path)
    assert load_pr(path) == pr
    assert isinstance(load_pr(path).references[0], Reference)


def test_save_json_supports_paths(tmp_path):
    path = tmp_path / "paths.json"
    save_json({"data": Path("data")}, path)
    assert json.loads(path.read_text()) == {"data": "data"}


def test_category_map_covers_all_categories():
    assert set(CATEGORIES) == {"logic_bug", "error_handling", "performance", "design_maintainability", "testing", "style_naming_docs"}
    assert set(AACR_CATEGORY_MAP) == set(CATEGORIES)
    assert set(AACR_CATEGORY_MAP.values()) <= {"Security", "Defect", "Maintainability", "Performance"}
