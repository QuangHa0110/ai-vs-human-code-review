"""Load local SWE-PRBench records and their substantive human annotations."""

import json
import random
import re
from collections import Counter
from hashlib import sha256
from pathlib import Path

from c3.models import PullRequest, Reference

_UPSTREAM_REVISION = "b87f5797aef3ed2c3153bb1304ea4d801d36ba6e"
_UPSTREAM = "https://huggingface.co/datasets/foundry-ai/swe-prbench"
_EVALUATED = Path("evals/eval_100.json")
_EVALUATED_FIELDS = ("repo", "pr_number", "base_commit", "head_commit", "diff_patch")
_TASK_ID = re.compile(r"[A-Za-z0-9_.-]+")
_REPO = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+")
_COMMIT = re.compile(r"[0-9a-fA-F]{40}")


def _source(path: Path, root: Path, payload: bytes) -> dict[str, str]:
    return {"name": path.relative_to(root).as_posix(), "sha256": sha256(payload).hexdigest()}


def _read_rows(path: Path, payload: bytes) -> dict[str, dict]:
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in payload.splitlines() if line.strip()]
    else:
        rows = json.loads(payload)
    if not isinstance(rows, list):
        raise ValueError(f"{path.name}: expected PR records")
    by_task = {}
    for row in rows:
        task = row.get("task_id") if isinstance(row, dict) else None
        if not isinstance(task, str) or not _TASK_ID.fullmatch(task) or task in {".", ".."}:
            raise ValueError(f"{path.name}: invalid or missing task_id")
        if task in by_task:
            raise ValueError(f"{path.name}: duplicate task_id {task}")
        by_task[task] = row
    return by_task


def _comment_id(value: object, path: Path) -> str:
    if (type(value) is int and value > 0) or (isinstance(value, str) and value.strip()):
        return str(value)
    raise ValueError(f"{path.name}: invalid comment ID")


def _references(
    path: Path, payload: bytes, pr_id: str, dropped: Counter[str],
) -> tuple[list[Reference], int]:
    annotation = json.loads(payload)
    if not isinstance(annotation, dict):
        raise ValueError(f"{path.name}: expected a human annotation object")
    substantive = annotation.get("substantive_comment_ids")
    comments = annotation.get("comments")
    if not isinstance(substantive, list) or not isinstance(comments, list):
        raise ValueError(f"{path.name}: expected substantive_comment_ids and comments arrays")

    by_id = {}
    for comment in comments:
        if not isinstance(comment, dict):
            raise ValueError(f"{path.name}: expected a comment object")
        comment_id = _comment_id(comment.get("comment_id"), path)
        if comment_id in by_id:
            raise ValueError(f"{path.name}: duplicate comment_id {comment_id}")
        by_id[comment_id] = comment
    substantive_ids = {_comment_id(value, path) for value in substantive}
    if len(substantive_ids) != len(substantive):
        raise ValueError(f"{path.name}: duplicate substantive comment IDs")
    missing = substantive_ids - by_id.keys()
    if missing:
        raise ValueError(f"{path.name}: unknown substantive comment IDs {', '.join(sorted(missing))}")

    refs = []
    for comment_id, comment in by_id.items():
        if comment_id not in substantive_ids:
            continue
        flags = ("is_reply", "is_initiating_comment", "is_in_diff")
        if any(type(comment.get(flag)) is not bool for flag in flags):
            raise ValueError(f"{path.name}: comment {comment_id} is missing annotation flags")
        line = comment.get("line")
        if comment["is_reply"] or not comment["is_initiating_comment"]:
            dropped["reply_or_non_initiating"] += 1
        elif not comment["is_in_diff"]:
            dropped["outside_diff"] += 1
        elif line is None:
            dropped["missing_line"] += 1
        else:
            file, body = comment.get("file"), comment.get("body")
            if type(line) is not int or line <= 0:
                raise ValueError(f"{path.name}: invalid line for comment {comment_id}")
            if not all(isinstance(value, str) and value.strip() for value in (file, body)):
                raise ValueError(f"{path.name}: expected file and body for comment {comment_id}")
            refs.append(Reference(f"{pr_id}:{comment_id}", file, line, body, "human", True))
    return refs, len(substantive_ids)


def _pull_request(task: str, row: dict) -> PullRequest:
    repo, number = row.get("repo"), row.get("pr_number")
    if not isinstance(repo, str) or not _REPO.fullmatch(repo) or repo.endswith(("/.", "/..")):
        raise ValueError(f"{task}: expected a GitHub owner/repo")
    if type(number) is not int or number <= 0:
        raise ValueError(f"{task}: expected a positive pr_number")
    expected_url = f"https://github.com/{repo}/pull/{number}"
    url = row.get("pr_url")
    if not isinstance(url, str) or url.rstrip("/").lower() != expected_url.lower():
        raise ValueError(f"{task}: pr_url disagrees with repo or pr_number")
    base, head = row.get("base_commit"), row.get("head_commit")
    if not all(isinstance(sha, str) and _COMMIT.fullmatch(sha) for sha in (base, head)):
        raise ValueError(f"{task}: expected full base_commit and head_commit SHAs")
    diff = row.get("diff_patch")
    if not isinstance(diff, str) or not diff.strip():
        raise ValueError(f"{task}: missing diff_patch")
    repo = repo.lower()
    return PullRequest(f"{repo}#{number}", "D3", repo, number, "", "", base, head, diff, [])


def load_swepr_with_audit(
    path: Path, languages: set[str], python_sample: int, seed: int, evaluated_only: bool = True,
) -> tuple[list[PullRequest], dict]:
    """Read selected PRs and record local input fingerprints and reference counts."""
    if type(python_sample) is not int or python_sample < 0:
        raise ValueError("python_sample must be a nonnegative integer")
    if not languages or not all(isinstance(language, str) and language.strip() for language in languages):
        raise ValueError("Specify at least one language with a nonempty name")

    is_eval_path = path.name == _EVALUATED.name and path.parent.name == _EVALUATED.parent.name
    root = path.parent.parent if is_eval_path else path.parent
    payload = path.read_bytes()
    rows = _read_rows(path, payload)
    inputs = {"prs": _source(path, root, payload), "annotations": {}}
    evaluation_path = root / _EVALUATED
    evaluated = None
    if is_eval_path:
        evaluated = rows
        inputs["evaluated"] = inputs["prs"]
    elif evaluation_path.is_file():
        evaluated_payload = evaluation_path.read_bytes()
        evaluated = _read_rows(evaluation_path, evaluated_payload)
        inputs["evaluated"] = _source(evaluation_path, root, evaluated_payload)
    elif evaluated_only:
        raise ValueError(f"{path.name}: evaluated membership requires {_EVALUATED.as_posix()}")

    candidates: Counter[str] = Counter()
    python = []
    selected = []
    for task, row in sorted(rows.items()):
        language = row.get("language")
        if not isinstance(language, str) or not language.strip():
            raise ValueError(f"{task}: missing language")
        if language not in languages or (evaluated_only and task not in evaluated):
            continue
        candidates[language] += 1
        (python if language == "Python" else selected).append(task)
    selected.extend(random.Random(seed).sample(python, min(python_sample, len(python))))

    prs = []
    sources = inputs["annotations"]
    seen_prs = set()
    dropped: Counter[str] = Counter()
    raw_count = 0
    for task in sorted(selected):
        row = rows[task]
        if not is_eval_path and evaluated is not None and task in evaluated:
            evaluated_row = evaluated[task]
            mismatched = [key for key in _EVALUATED_FIELDS if row.get(key) != evaluated_row.get(key)]
            if mismatched:
                raise ValueError(f"{task}: snapshot differs from {_EVALUATED.name} in {', '.join(mismatched)}")
        pr = _pull_request(task, row)
        if pr.pr_id in seen_prs:
            raise ValueError(f"{task}: duplicate PR {pr.pr_id}")
        seen_prs.add(pr.pr_id)
        annotation = root / "annotations" / f"{task}_human.json"
        annotation_payload = annotation.read_bytes()
        sources[task] = _source(annotation, root, annotation_payload)
        pr.references, raw = _references(annotation, annotation_payload, pr.pr_id, dropped)
        raw_count += raw
        prs.append(pr)

    audit = {
        "inputs": inputs,
        "upstream": {"dataset": _UPSTREAM, "documented_revision": _UPSTREAM_REVISION},
        "languages": sorted(languages),
        "python_sample": python_sample,
        "seed": seed,
        "evaluated_only": evaluated_only,
        "evaluated_membership": {task: task in evaluated if evaluated is not None else None for task in sorted(selected)},
        "candidate_prs_by_language": dict(candidates),
        "selected_prs_by_language": dict(Counter(rows[task]["language"] for task in selected)),
        "selected_pr_ids": [pr.pr_id for pr in prs],
        "raw_substantive_references": raw_count,
        "retained_references": sum(len(pr.references) for pr in prs),
        "dropped_references": dict(dropped),
        "prs_without_references": [pr.pr_id for pr in prs if not pr.references],
    }
    return prs, audit


def load_swepr(
    path: Path, languages: set[str], python_sample: int, seed: int, evaluated_only: bool = True,
) -> list[PullRequest]:
    """Load all eligible non-Python PRs and a seeded sample of eligible Python PRs."""
    prs, _ = load_swepr_with_audit(path, languages, python_sample, seed, evaluated_only)
    return prs
