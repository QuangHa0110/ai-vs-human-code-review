"""Create a shallow, detached snapshot at one reviewed commit."""

import shutil
import re
import subprocess
from pathlib import Path


_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")


def _git(directory: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(directory), *args], check=True,
                            capture_output=True, text=True, timeout=120)
    return result.stdout.strip()


def prepare_checkout(repo_url: str, sha: str, dest: Path) -> Path:
    """Fetch exactly ``sha`` into an otherwise empty shallow clone.

    The destination is never reused or cleared. If setup fails after this
    function creates it, only that newly-created directory is removed.
    """
    dest = Path(dest)
    if not isinstance(repo_url, str) or not repo_url.strip():
        raise ValueError("repo_url must be a nonempty URL")
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise ValueError("sha must be a full 40-character commit ID")
    if dest.exists() or dest.is_symlink():
        raise RuntimeError(f"checkout destination is occupied: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.mkdir()
    try:
        _git(dest, "init", "--quiet")
        _git(dest, "remote", "add", "origin", repo_url)
        try:
            _git(dest, "fetch", "--quiet", "--depth=1", "--no-tags", "origin", sha)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"fetch failed for requested commit {sha}") from exc
        _git(dest, "checkout", "--quiet", "--detach", "FETCH_HEAD")
        actual = _git(dest, "rev-parse", "HEAD")
        count = _git(dest, "rev-list", "--count", "HEAD")
        if actual != sha or count != "1":
            raise RuntimeError(f"checkout verification failed: expected {sha}, got {actual} ({count} commits)")
        return dest
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
