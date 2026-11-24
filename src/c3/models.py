"""Shared records and UTF-8 JSON persistence."""

import json
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Literal

CATEGORIES = (
    "logic_bug", "error_handling", "performance", "design_maintainability",
    "testing", "style_naming_docs",
)

# Upstream taxonomy: Security / Defect / Maintainability / Performance.
# Source: https://github.com/alibaba/aacr-bench/blob/main/README.md (2026-11-24).
# This is a provisional C3 aggregation, not an upstream-provided crosswalk.
# C3 has no dedicated Security category: security findings require manual
# classification before four-way comparisons; do not invent a security mapping.
AACR_CATEGORY_MAP: dict[str, str] = {
    "logic_bug": "Defect",
    "error_handling": "Defect",
    "performance": "Performance",
    "design_maintainability": "Maintainability",
    "testing": "Maintainability",
    "style_naming_docs": "Maintainability",
}


@dataclass
class Reference:
    ref_id: str
    path: str
    line: int | None
    text: str
    origin: Literal["human", "ai"]
    valid: bool | None
    category: str | None = None
    actionable: bool | None = None


@dataclass
class PullRequest:
    pr_id: str
    dataset: Literal["D1", "D2", "D3"]
    repo: str
    number: int
    title: str
    body: str
    base_sha: str
    review_sha: str
    diff: str
    references: list[Reference]


@dataclass
class AiComment:
    path: str
    line: int | None
    comment: str
    category: str


def _json_default(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def save_json(obj: Any, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, default=_json_default, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_pr(path: Path) -> PullRequest:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data["references"] = [Reference(**ref) for ref in data["references"]]
    return PullRequest(**data)
