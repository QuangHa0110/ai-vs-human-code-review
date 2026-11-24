"""Read public AACR-Bench v1 PRs and audit reference filtering."""

import json
import re
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Literal

from c3.models import PullRequest, Reference

_REVISION = "07ba1b35102110404f6a931bf05dea33e6d89389"
_PINNED_SHA256 = {
    "positive": "02c8561f1c3994a26f427d479b2a3f6e6c8e6858161fbc903b45248fa2cb2097",
    "negative": "d4e9f5b610033914e5e36b066ee4c6177f037642a011949f68dd4e20bfa77e2b",
}
_PR_URL = re.compile(r"https://github\.com/([a-z0-9-]+/[a-z0-9_.-]+)/pull/([1-9][0-9]*)/?", re.IGNORECASE | re.ASCII)
_COMMIT = re.compile(r"[0-9a-fA-F]{40}")


def _reference_counts() -> dict[str, dict[str, int]]:
    return {split: {"human": 0, "ai": 0} for split in _PINNED_SHA256}


def summary(prs: list[PullRequest]) -> dict:
    """Count records by repository and references by validity and origin."""
    repos = Counter(pr.repo for pr in prs)
    counts = _reference_counts()
    for pr in prs:
        for ref in pr.references:
            if ref.valid is not None:
                counts["positive" if ref.valid else "negative"][ref.origin] += 1
    return {"prs": len(prs), "repos": len(repos), "prs_by_repo": dict(sorted(repos.items())), "references": counts}


def _reference(raw: object, location: str, ref_id: str, valid: bool) -> tuple[Reference, str]:
    if not isinstance(raw, dict):
        raise ValueError(f"{location}: expected a comment object")
    for name in ("note", "path", "category"):
        if not isinstance(raw.get(name), str) or not raw[name].strip():
            raise ValueError(f"{location}: expected a nonempty {name}")
    if type(raw.get("is_ai_comment")) is not bool:
        raise ValueError(f"{location}: expected a boolean is_ai_comment")
    side = raw.get("side")
    if not isinstance(side, str) or side.upper() not in {"LEFT", "RIGHT"}:
        raise ValueError(f"{location}: expected side LEFT or RIGHT")
    line = raw.get("to_line")
    if "to_line" not in raw or (line is not None and (type(line) is not int or line <= 0)):
        raise ValueError(f"{location}: expected to_line to be a positive integer or null")
    origin: Literal["ai", "human"] = "ai" if raw["is_ai_comment"] else "human"
    return Reference(
        ref_id=ref_id, path=raw["path"], line=line, text=raw["note"],
        origin=origin, valid=valid, category=raw["category"],
    ), side.upper()


def load_aacr_with_audit(
    positive: Path, negative: Path, language: str = "Java",
) -> tuple[list[PullRequest], dict]:
    """Load PRs and report source fingerprints and counts before line/file filters."""
    paths = {"positive": positive, "negative": negative}
    payloads = {split: path.read_bytes() for split, path in paths.items()}
    digests = {split: sha256(payload).hexdigest() for split, payload in payloads.items()}
    if digests["positive"] == digests["negative"]:
        raise ValueError("The positive and negative AACR input files have identical content")
    for split, other in (("positive", "negative"), ("negative", "positive")):
        if digests[split] == _PINNED_SHA256[other]:
            raise ValueError(f"The pinned {other} AACR input was supplied as --{split}")

    grouped: dict[str, PullRequest] = {}
    metadata: dict[str, tuple[str, str, str]] = {}
    raw_counts = _reference_counts()
    split_prs = {}
    dropped: Counter[str] = Counter()
    sources = {}

    for split, path in paths.items():
        digest = digests[split]
        sources[split] = {"name": path.name, "sha256": digest}
        if digest == _PINNED_SHA256[split]:
            sources[split]["url"] = (
                f"https://github.com/alibaba/aacr-bench/blob/{_REVISION}/dataset/v1/{split}_samples.json"
            )
        rows = json.loads(payloads[split])
        if not isinstance(rows, list):
            raise ValueError(f"{path.name}: expected an array of PRs")
        per_pr_indices: Counter[str] = Counter()
        for row_number, row in enumerate(rows, start=1):
            location = f"{path.name} row {row_number}"
            if not isinstance(row, dict):
                raise ValueError(f"{location}: expected a PR object")
            url = row.get("githubPrUrl")
            match = _PR_URL.fullmatch(url) if isinstance(url, str) else None
            if match is None or match[1].endswith(("/.", "/..")):
                raise ValueError(f"{location}: expected a GitHub PR URL in githubPrUrl")
            repo, number = match[1].lower(), int(match[2])
            pr_id = f"{repo}#{number}"
            row_language = row.get("project_main_language")
            if not isinstance(row_language, str) or not row_language.strip():
                raise ValueError(f"{location}: expected a nonempty project_main_language")
            for key in ("source_commit", "target_commit"):
                if not isinstance(row.get(key), str) or _COMMIT.fullmatch(row[key]) is None:
                    raise ValueError(f"{location}: expected a full SHA in {key}")
            base, review = row["source_commit"].lower(), row["target_commit"].lower()
            row_metadata = (row_language, base, review)
            if metadata.setdefault(pr_id, row_metadata) != row_metadata:
                raise ValueError(f"{location}: inconsistent language or commit pair for {pr_id}")
            if row_language != language:
                continue
            comments = row.get("comments")
            if not isinstance(comments, list):
                raise ValueError(f"{location}: expected a comments array")
            if pr_id not in grouped:
                grouped[pr_id] = PullRequest(
                    pr_id=pr_id, dataset="D1", repo=repo, number=number, title="", body="",
                    base_sha=base, review_sha=review, diff="", references=[],
                )
            pr = grouped[pr_id]
            first_index = per_pr_indices[pr_id]
            per_pr_indices[pr_id] += len(comments)
            for offset, raw in enumerate(comments):
                ref, side = _reference(
                    raw, f"{location}, comment {offset + 1}",
                    f"{pr_id}:{split[0]}{first_index + offset}", split == "positive",
                )
                raw_counts[split][ref.origin] += 1
                if language == "Java" and not ref.path.endswith(".java"):
                    dropped["not_java"] += 1
                elif side == "LEFT":
                    dropped["left_side"] += 1
                elif ref.line is None:
                    dropped["missing_line"] += 1
                else:
                    pr.references.append(ref)
        split_prs[split] = len(per_pr_indices)

    audit = {
        "inputs": sources,
        "language": language,
        "raw": {"prs": len(grouped), "split_prs": split_prs, "references": raw_counts},
        "dropped_references": dict(dropped),
    }
    if all("url" in source for source in sources.values()):
        audit["upstream_revision"] = _REVISION
    return list(grouped.values()), audit


def load_aacr(positive: Path, negative: Path, language: str = "Java") -> list[PullRequest]:
    """Load local AACR-Bench records with an empty diff for later hydration."""
    prs, _ = load_aacr_with_audit(positive, negative, language)
    return prs
