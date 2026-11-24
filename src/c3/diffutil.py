"""Select Java file blocks and count changed lines in unified diffs."""

import re
from collections.abc import Iterable, Iterator
from io import StringIO

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
_JAVA_GIT_DESTINATION = re.compile(r' "?b/.*\.java"?$')


def _diff_lines(lines: Iterable[str]) -> Iterator[tuple[int, str, bool]]:
    """Mark hunk lines using the counts in each unified hunk header."""
    remaining = 0
    for index, line in enumerate(lines):
        header = _HUNK_HEADER.match(line)
        if header:
            old, new = header.groups()
            remaining = int(old or "1") + int(new or "1")
        elif remaining and line.startswith(("+", "-", " ")):
            remaining -= 2 if line.startswith(" ") else 1
            yield index, line, True
            continue
        yield index, line, False


def _file_starts(lines: list[str]) -> list[int]:
    git_starts = [index for index, line in enumerate(lines) if line.startswith("diff --git ")]
    if git_starts:
        return git_starts
    return [
        index
        for index, line, in_hunk in _diff_lines(lines)
        if not in_hunk
        and line.startswith("--- ")
        and index + 1 < len(lines)
        and lines[index + 1].startswith("+++ ")
    ]


def _is_java_block(lines: list[str]) -> bool:
    old_path = ""
    for line in lines:
        if line.startswith("@@ "):
            break
        if line.startswith("--- "):
            old_path = line[4:].rstrip("\r\n").split("\t", 1)[0]
        elif line.startswith("+++ "):
            new_path = line[4:].rstrip("\r\n").split("\t", 1)[0]
            path = old_path if new_path == "/dev/null" else new_path
            # Git quotes paths with special characters. The ASCII suffix stays literal.
            return path.removesuffix('"').endswith(".java")
        elif line.startswith(("rename to ", "copy to ")):
            return line.rstrip("\r\n").removesuffix('"').endswith(".java")
    return _JAVA_GIT_DESTINATION.search(lines[0].rstrip("\r\n")) is not None


def filter_java(diff: str) -> str:
    """Keep blocks for Java destinations and files deleted from Java paths."""
    lines = StringIO(diff).readlines()
    starts = _file_starts(lines)
    ends = starts[1:] + [len(lines)]
    kept = []
    for start, end in zip(starts, ends):
        block = lines[start:end]
        if _is_java_block(block):
            kept.extend(block)
    return "".join(kept)


def changed_lines(diff: str) -> int:
    return sum(
        in_hunk and line.startswith(("+", "-"))
        for _, line, in_hunk in _diff_lines(StringIO(diff))
    )
