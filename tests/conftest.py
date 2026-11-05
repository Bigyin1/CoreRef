"""Real Conan instances and Git fixtures use owned temporary directories."""

from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.objects import Blob, Commit, ObjectID, Tree
from dulwich.refs import Ref as GitRef
from dulwich.repo import Repo

from examples.conan_config import configured_home
from hwcatalog.api.catalog import Catalog


@pytest.fixture
def catalog(tmp_path: Path) -> Iterator[Catalog]:
    with Catalog(configured_home(tmp_path / "conan-home")) as catalog:
        yield catalog


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    path = tmp_path / "checkout"
    path.mkdir()
    with Repo.init(path) as repo:
        blob = Blob.from_string(b"module cpu; endmodule\n")
        tree = Tree()
        tree.add(b"cpu.sv", 0o100644, ObjectID(blob.id))
        repo.object_store.add_object(blob)
        repo.object_store.add_object(tree)
        commit = Commit()
        commit.tree = tree.id
        commit.author = commit.committer = b"Test <test@example.invalid>"
        commit.author_time = commit.commit_time = 1
        commit.author_timezone = commit.commit_timezone = 0
        commit.message = b"RTL source"
        repo.object_store.add_object(commit)
        repo.refs[GitRef(b"refs/heads/main")] = ObjectID(commit.id)
        repo.refs.set_symbolic_ref(GitRef(b"HEAD"), GitRef(b"refs/heads/main"))
        repo.get_worktree().reset_index(tree.id)
    return path


@pytest.fixture
def git_origin(checkout: Path, tmp_path: Path) -> Path:
    """A separate authoritative Git remote, standing in for GitLab in local tests."""
    remote = tmp_path / "gitlab-origin.git"
    with porcelain.clone(str(checkout), remote, bare=True, errstream=BytesIO()):
        pass
    with Repo(checkout) as repo:
        config = repo.get_config()
        config.set((b"remote", b"origin"), b"url", str(remote).encode())
        config.write_to_path()
        branches, _ = repo.refs.follow(GitRef(b"HEAD"))
        branch = branches[-1].removeprefix(b"refs/heads/")
        repo.refs[GitRef(b"refs/remotes/origin/" + branch)] = repo.head()
    return remote


@pytest.fixture(autouse=True)
def isolated_default_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONAN_DEFAULT_PROFILE", raising=False)
    monkeypatch.delenv("CONAN_DEFAULT_BUILD_PROFILE", raising=False)
