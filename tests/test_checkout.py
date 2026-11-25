import subprocess

import pytest


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


@pytest.fixture
def history(tmp_path):
    repo = tmp_path / "origin"
    repo.mkdir()
    git(repo, "init", "-q")
    shas = []
    for number in range(3):
        (repo / f"file-{number}").write_text(str(number))
        git(repo, "add", ".")
        git(repo, "-c", "user.email=test@example.org", "-c", "user.name=Fixture",
            "commit", "-qm", f"commit {number}")
        shas.append(git(repo, "rev-parse", "HEAD"))
    return repo, shas


def test_checkout_contains_only_target_commit(history, tmp_path):
    from c3.checkout import prepare_checkout

    repo, (first, target, future) = history
    dest = tmp_path / "snapshot"
    assert prepare_checkout(repo.as_uri(), target, dest) == dest
    assert git(dest, "rev-parse", "HEAD") == target
    assert git(dest, "rev-list", "--count", "HEAD") == "1"
    assert git(dest, "for-each-ref") == ""
    assert git(dest, "branch", "--show-current") == ""
    assert sorted(path.name for path in dest.iterdir()) == [".git", "file-0", "file-1"]
    for sha in (first, future):
        with pytest.raises(subprocess.CalledProcessError):
            git(dest, "cat-file", "-e", sha)


def test_checkout_wrong_sha_raises(history, tmp_path):
    from c3.checkout import prepare_checkout

    repo, _ = history
    dest = tmp_path / "snapshot"
    with pytest.raises(RuntimeError, match="fetch"):
        prepare_checkout(repo.as_uri(), "2" * 40, dest)
    assert not dest.exists()


def test_checkout_rejects_non_40_character_sha(history, tmp_path):
    from c3.checkout import prepare_checkout

    repo, _ = history
    dest = tmp_path / "snapshot"
    with pytest.raises(ValueError, match="40-character"):
        prepare_checkout(repo.as_uri(), "a" * 64, dest)
    assert not dest.exists()


@pytest.mark.parametrize("kind", ["directory", "symlink", "file"])
def test_checkout_preserves_occupied_destination(history, tmp_path, kind):
    from c3.checkout import prepare_checkout

    repo, (_, target, _) = history
    protected = tmp_path / "protected"
    protected.mkdir()
    marker = protected / "contents"
    marker.write_text("keep")
    dest = tmp_path / "snapshot"
    if kind == "symlink":
        dest.symlink_to(protected, target_is_directory=True)
    elif kind == "file":
        dest.write_text("keep")
    else:
        dest = protected
    with pytest.raises(RuntimeError, match="occupied"):
        prepare_checkout(repo.as_uri(), target, dest)
    assert marker.read_text() == "keep"
    assert not (protected / ".git").exists()
    if kind == "file":
        assert dest.read_text() == "keep"
    elif kind == "symlink":
        assert dest.is_symlink()
