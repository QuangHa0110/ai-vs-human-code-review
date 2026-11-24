"""Filter GitHub review comments for the D2 reference dataset."""

import re
from collections import Counter
from dataclasses import dataclass

from c3.models import Reference

SUBSTANCE_KEYWORDS = (
    "bug", "cache", "complexity", "concurrency", "deadlock", "duplicate", "edge case",
    "error", "exception", "failure", "incorrect", "leak", "memory", "null", "overflow",
    "performance", "race condition", "regression", "test", "tests", "thread", "validation",
)
_KEYWORD = re.compile(r"\b(?:" + "|".join(map(re.escape, SUBSTANCE_KEYWORDS)) + r")\b", re.IGNORECASE)
_CODE = re.compile(r"`|\b[a-z][a-z0-9]*[A-Z]\w*\b|\b[A-Za-z_$][\w$]*\([^)]*\)")


@dataclass
class CleanResult:
    kept: list[Reference]
    dropped: dict[str, int]
    review_sha: str | None
    bot_ratio: float


def is_substantive(body: str) -> bool:
    return len(body.split()) >= 10 and (_CODE.search(body) is not None or _KEYWORD.search(body) is not None)


def _review_line(comment: dict) -> int | None:
    current_line = comment.get("line")
    original_sha = comment.get("original_commit_id")
    if current_line is None or not original_sha:
        return None
    if comment.get("original_line") is not None:
        return comment["original_line"]
    if comment.get("commit_id") in (None, original_sha):
        return current_line
    return None


def _drop_reason(comment: dict, pr_author: str, review_line: int | None) -> str | None:
    user = comment.get("user") or {}
    login = (user.get("login") or "").casefold()
    if user.get("type") == "Bot" or login.endswith("[bot]"):
        return "bot"
    if comment.get("in_reply_to_id") is not None:
        return "reply"
    if login and login == pr_author.casefold():
        return "author"
    if not (comment.get("path") or "").endswith(".java"):
        return "not_java"
    if comment.get("side") == "LEFT":
        return "left_side"
    if review_line is None:
        return "outdated"
    if not is_substantive(comment.get("body") or ""):
        return "not_substantive"
    return None


def clean_comments(raw: list[dict], pr_author: str, pr_id: str) -> CleanResult:
    """Keep substantive roots on the first reviewed commit. Bot ratio uses all raw comments."""
    dropped: Counter[str] = Counter()
    candidates = []
    for comment in raw:
        line = _review_line(comment)
        reason = _drop_reason(comment, pr_author, line)
        if reason:
            dropped[reason] += 1
        else:
            candidates.append((comment, line))

    candidates.sort(key=lambda candidate: (candidate[0]["created_at"], candidate[0]["id"]))
    review_sha = candidates[0][0]["original_commit_id"] if candidates else None
    kept = []
    for comment, line in candidates:
        if comment["original_commit_id"] != review_sha:
            dropped["later_round"] += 1
            continue
        kept.append(Reference(
            ref_id=f"{pr_id}:{comment['id']}",
            path=comment["path"],
            line=line,
            text=comment["body"],
            origin="human",
            valid=True,
        ))

    bot_ratio = dropped["bot"] / len(raw) if raw else 0.0
    return CleanResult(kept=kept, dropped=dict(dropped), review_sha=review_sha, bot_ratio=bot_ratio)
