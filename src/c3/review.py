"""Prompt construction and strict parsing for AI code reviews."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from c3.models import AiComment, CATEGORIES, PullRequest
from c3.runners import CachedRunner

SYSTEM_PROMPT = """Review the supplied pull request diff for actionable defects or maintainability problems.
Return only a JSON array. Each item must have exactly these keys: file, line, comment, category.
Use a repository-relative file path, a positive line number or null, a concise explanation,
and one category from: logic_bug, error_handling, performance, design_maintainability,
testing, style_naming_docs. Return [] when there are no actionable findings."""
AGENT_SENTENCE = "You may read files in the repository to understand the diff; do not inspect history or other commits."


@dataclass
class ReviewResult:
    pr_id: str
    reviewer: str
    mode: str
    run_idx: int
    comments: list[AiComment]
    meta: dict
    error: str | None = None
    parse_error: bool = False


def build_review_prompt(pr: PullRequest, mode: str) -> tuple[str, str]:
    if mode not in {"diff", "agent"}:
        raise ValueError("mode must be 'diff' or 'agent'")
    system = SYSTEM_PROMPT + ("\n" + AGENT_SENTENCE if mode == "agent" else "")
    return system, f"Review this pull request diff:\n\n{pr.diff}"


def _parse_comments(text: str) -> tuple[list[AiComment], int]:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(text, object_pairs_hook=unique_pairs)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("review output must be a JSON array")
    comments = []
    unknown = 0
    for index, item in enumerate(payload):
        if not isinstance(item, dict) or set(item) != {"file", "line", "comment", "category"}:
            raise ValueError(f"comment {index} must contain exactly file, line, comment, category")
        file, line, comment, category = (item["file"], item["line"], item["comment"], item["category"])
        if not isinstance(file, str) or not file.strip() or "\x00" in file or "\\" in file:
            raise ValueError(f"comment {index} file must be a relative POSIX path")
        path = PurePosixPath(file)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in file.split("/")) or PureWindowsPath(file).drive:
            raise ValueError(f"comment {index} file must not be absolute or traverse directories")
        if line is not None and (isinstance(line, bool) or not isinstance(line, int) or line < 1):
            raise ValueError(f"comment {index} line must be a positive integer or null")
        if not isinstance(comment, str) or not isinstance(category, str):
            raise ValueError(f"comment {index} comment and category must be strings")
        if category not in CATEGORIES:
            category = "design_maintainability"
            unknown += 1
        comments.append(AiComment(path.as_posix(), line, comment, category))
    return comments, unknown


def _version(runner) -> str | None:
    version = getattr(runner, "tool_version", None)
    if version:
        return str(version)
    inner = getattr(runner, "inner", None)
    version = getattr(inner, "tool_version", None)
    return str(version) if version else None


def _cache_context(pr: PullRequest, mode: str, system: str, user: str) -> str:
    return json.dumps({"repo": pr.repo, "base_sha": pr.base_sha, "head_sha": pr.review_sha,
                       "diff": pr.diff, "mode": mode, "system": system, "user": user},
                      sort_keys=True, ensure_ascii=False)


def run_review(runner, pr: PullRequest, reviewer: str, mode: str, run_idx: int,
               workdir: Path | None, retries: int = 2) -> ReviewResult:
    if mode not in {"diff", "agent"}:
        raise ValueError("mode must be 'diff' or 'agent'")
    if isinstance(run_idx, bool) or not isinstance(run_idx, int) or run_idx < 1:
        raise ValueError("run_idx must be a positive integer")
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ValueError("retries must be a nonnegative integer")
    if mode == "agent" and workdir is None:
        raise ValueError("agent mode requires a repository snapshot workdir")
    if mode == "diff" and workdir is not None and any(Path(workdir).iterdir()):
        raise ValueError("diff mode workdir must be empty")

    system, user = build_review_prompt(pr, mode)
    allow_read_tools = mode == "agent"
    context = _cache_context(pr, mode, system, user)
    model = getattr(getattr(runner, "spec", None), "model", "")
    if not model:
        model = getattr(getattr(runner, "inner", None), "spec", None)
        model = getattr(model, "model", "")
    version = _version(runner)
    attempts = []
    latest_meta = {}
    last_error = None

    for attempt_idx in range(retries + 1):
        current = runner
        if isinstance(runner, CachedRunner) and version and model:
            current = runner.with_context(run_idx=run_idx, attempt_idx=attempt_idx,
                                          context=context, model=model, tool_version=version)
        output = current.run(system, user, workdir, allow_read_tools)
        latest_meta = output.meta if isinstance(output.meta, dict) else {}
        if output.error is not None:
            return ReviewResult(pr.pr_id, reviewer, mode, run_idx, [],
                                {**latest_meta, "attempts": attempts}, output.error, False)
        try:
            comments, unknown_count = _parse_comments(output.text)
            attempts.append({"text": output.text, "meta": latest_meta})
            return ReviewResult(pr.pr_id, reviewer, mode, run_idx, comments,
                                {**latest_meta, "attempts": attempts,
                                 "unknown_category_count": unknown_count}, None, False)
        except ValueError as exc:
            last_error = str(exc)
            attempts.append({"text": output.text, "meta": latest_meta, "parse_error": last_error})

    return ReviewResult(pr.pr_id, reviewer, mode, run_idx, [],
                        {**latest_meta, "attempts": attempts}, None, True)
