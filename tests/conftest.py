"""In-memory instances provide the isolated dependency API contract."""

from collections.abc import Callable, Iterator
from io import BytesIO
from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.objects import Blob, Commit, ObjectID, Tree
from dulwich.refs import Ref as GitRef
from dulwich.repo import Repo

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.protocols import Backend
from hwcatalog.backends.memory import MemoryBackend

type Factory = Callable[[], Backend]


@pytest.fixture
def backend_factory() -> Iterator[Factory]:
    clients: list[Backend] = []

    def create() -> Backend:
        backend = MemoryBackend()
        clients.append(backend)
        return backend

    yield create
    for client in clients:
        client.close()


@pytest.fixture
def catalog(backend_factory: Factory) -> Catalog:
    return Catalog(backend_factory())


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
