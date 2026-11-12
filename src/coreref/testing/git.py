"""Local Git and file fixtures, without shell commands or user identity settings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from dulwich import porcelain
from dulwich.refs import Ref as GitRef
from dulwich.repo import Repo


@dataclass(frozen=True)
class FixtureFile:
    """Fixture contents with an explicit mode, e.g. FixtureFile(script, 0o755)."""

    content: str | bytes
    mode: int = 0o644


def write_files(directory: Path, files: Mapping[str, str | bytes | FixtureFile]) -> dict[str, Path]:
    paths = {}
    for name, content in files.items():
        path = directory / name
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError(f"Fixture file must stay inside its directory: {name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        value = content.content if isinstance(content, FixtureFile) else content
        path.write_bytes(value.encode() if isinstance(value, str) else value)
        if isinstance(content, FixtureFile):
            path.chmod(content.mode)
        paths[name] = path
    return paths


def git_repository(
    directory: Path,
    files: Mapping[str, str | bytes | FixtureFile],
    *,
    reuse: bool = False,
) -> Path:
    """Initialize an existing directory, or explicitly reuse its Git history.

    An existing repository is preserved unless reuse=True; reuse adds a commit,
    never resets or deletes history. Only supplied files are staged.
    """
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / ".git").exists():
        if not reuse:
            raise FileExistsError(f"Git repository already exists: {directory}")
    else:
        with porcelain.init(directory) as repo:
            repo.refs.set_symbolic_ref(GitRef(b"HEAD"), GitRef(b"refs/heads/main"))
    git_commit(directory, files, message="Test sources")
    return directory


def git_commit(
    directory: Path,
    files: Mapping[str, str | bytes | FixtureFile],
    *,
    message: str = "Test changes",
    author: str = "Test <test@example.invalid>",
    push: bool = False,
) -> str:
    """Write/stage changes, commit with explicit identity, optionally push to origin."""
    write_files(directory, files)
    porcelain.add(directory, paths=list(files))
    revision = porcelain.commit(
        directory,
        message=message.encode(),
        author=author.encode(),
        committer=author.encode(),
        sign=False,
    )
    if push:
        porcelain.push(directory, "origin", errstream=BytesIO())
    return revision.decode()


def git_upstream(checkout: Path, directory: Path) -> Path:
    """Create a bare origin for an existing checkout, including tracking refs.

    The destination must not exist. Tests can commit and push subsequent changes
    with Dulwich or Git, just as they would with a remote repository.
    """
    directory = directory.resolve()
    directory.mkdir(parents=True)
    with porcelain.clone(str(checkout), directory, bare=True, errstream=BytesIO()):
        pass
    with Repo(checkout) as repo:
        config = repo.get_config()
        config.set((b"remote", b"origin"), b"url", str(directory).encode())
        branches, _ = repo.refs.follow(GitRef(b"HEAD"))
        branch = branches[-1].removeprefix(b"refs/heads/")
        config.set((b"branch", branch), b"remote", b"origin")
        config.set((b"branch", branch), b"merge", b"refs/heads/" + branch)
        config.write_to_path()
        repo.refs[GitRef(b"refs/remotes/origin/" + branch)] = repo.head()
    return directory
