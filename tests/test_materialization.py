"""Real cached checkouts and local resource paths, without external Git or network."""

import sys
from dataclasses import FrozenInstanceError, asdict, dataclass, replace
from pathlib import Path

import pytest
from dulwich.objects import Blob, Commit, ObjectID, Tree
from dulwich.refs import Ref
from dulwich.repo import Repo

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import MaterializationError
from hwcatalog.api.models.records import Record
from hwcatalog.api.models.resources import GitResource, LocatedResource, MaterializedResource
from tests.helpers import release


@dataclass(frozen=True)
class History:
    source: Path
    first: str
    latest: str

    def resource(self, *, commit: str | None = None) -> GitResource:
        return GitResource(str(self.source), "main", commit or self.first)


def add_commit(repository: Repo, content: bytes, parents: list[ObjectID]) -> ObjectID:
    blob = Blob.from_string(content)
    subtree = Tree()
    object_format = repository.object_format
    subtree.add(b"design.v", 0o100644, ObjectID(blob.get_id(object_format)))
    tree = Tree()
    tree.add(b"rtl", 0o040000, ObjectID(subtree.get_id(object_format)))
    tree.add(b"README", 0o100644, ObjectID(blob.get_id(object_format)))
    for obj in (blob, subtree, tree):
        repository.object_store.add_object(obj)
    commit = Commit()
    commit.tree = tree.get_id(object_format)
    commit.parents = parents
    commit.author = commit.committer = b"Tests <tests@example.invalid>"
    commit.author_time = commit.commit_time = len(parents) + 1
    commit.author_timezone = commit.commit_timezone = 0
    commit.message = content
    repository.object_store.add_object(commit)
    return ObjectID(commit.get_id(object_format))


@pytest.fixture(params=[(False, "sha1"), (True, "sha1"), (False, "sha256"), (True, "sha256")])
def history(request: pytest.FixtureRequest, tmp_path: Path) -> History:
    bare, object_format = request.param
    source = tmp_path / "origin"
    source.mkdir()
    initialize = Repo.init_bare if bare else Repo.init
    with initialize(source, object_format=object_format) as repo:
        first = add_commit(repo, b"first", [])
        latest = add_commit(repo, b"latest", [first])
        repo.refs[Ref(b"refs/heads/main")] = latest
        repo.refs.set_symbolic_ref(Ref(b"HEAD"), Ref(b"refs/heads/main"))
    return History(source, first.decode("ascii"), latest.decode("ascii"))


def snapshot(root: Path) -> dict[Path, tuple[bytes, int]]:
    return {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and not path.name.endswith(".lock")
    }


def test_checkout_pinned_commit_and_noop_offline(history: History, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    resource = history.resource()
    result = resource.materialize(str(cache))
    assert result.path.parent == cache / "git"
    assert (result.path / "rtl" / "design.v").read_text() == "first"
    with Repo(result.path) as repo:
        assert repo.head().decode("ascii") == history.first
        assert repo.refs.read_ref(Ref(b"HEAD")) == history.first.encode("ascii")
    before = snapshot(result.path)
    history.source.rename(tmp_path / "offline")
    assert resource.materialize(cache) == result
    assert snapshot(result.path) == before
    assert replace(resource, requested_ref="missing-ref").materialize(cache) == result
    assert snapshot(result.path) == before
    with pytest.raises(FrozenInstanceError):
        result.path = cache  # type: ignore[misc]


def test_distinct_commits_get_distinct_copies(history: History, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    old = history.resource().materialize(cache)
    new = history.resource(commit=history.latest).materialize(cache)
    assert old.path != new.path
    assert (old.path / "rtl" / "design.v").read_text() == "first"
    assert (new.path / "rtl" / "design.v").read_text() == "latest"
    alias = replace(history.resource(), repository_url=history.source.as_uri()).materialize(cache)
    assert alias.path not in (old.path, new.path)


def test_missing_commit_cleans_staging(history: History, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    with pytest.raises(MaterializationError, match="Cannot materialize"):
        replace(history.resource(), resolved_commit="0" * len(history.first)).materialize(cache)
    assert not list((cache / "git").iterdir())
    assert history.resource().materialize(cache).path.is_dir()


def test_cache_reuses_local_changes(history: History, tmp_path: Path) -> None:
    resource = history.resource()
    result = resource.materialize(tmp_path / "cache")
    (result.path / "README").write_text("local changes")
    (result.path / "untracked").write_text("extra file")
    before = snapshot(result.path)
    assert resource.materialize(tmp_path / "cache") == result
    assert snapshot(result.path) == before


def test_cache_requires_requested_commit(history: History, tmp_path: Path) -> None:
    resource = history.resource()
    result = resource.materialize(tmp_path / "cache")
    with Repo(result.path) as repo:
        repo.refs[Ref(b"HEAD")] = ObjectID(history.latest.encode("ascii"))
    before = snapshot(result.path)
    with pytest.raises(MaterializationError, match="different commit"):
        resource.materialize(tmp_path / "cache")
    assert snapshot(result.path) == before


def test_stored_resources_materialize(history: History, catalog: Catalog, tmp_path: Path) -> None:
    record = replace(
        release("materialize"),
        payload=(asdict(history.resource()), asdict(LocatedResource("nfs", str(tmp_path)))),
    )
    catalog.publish((record,))
    stored = catalog.get(record.ref)
    assert isinstance(stored, Record)
    from collections.abc import Mapping

    from pydantic import TypeAdapter

    adapter = TypeAdapter(GitResource | LocatedResource)
    assert isinstance(stored.payload, tuple)
    for payload in stored.payload:
        assert isinstance(payload, Mapping)
        resource = adapter.validate_python(dict(payload))
        result = resource.materialize(tmp_path / "cache")
        if isinstance(resource, GitResource):
            assert (result.path / "rtl" / "design.v").read_text() == "first"
        else:
            assert result.path == tmp_path
        assert resource.materialize(tmp_path / "cache") == result


@pytest.mark.parametrize("provider", ["local", "file", "nfs"])
@pytest.mark.parametrize("use_uri", [False, True])
def test_local_resource_paths(tmp_path: Path, provider: str, use_uri: bool) -> None:
    target = tmp_path / "mounted data"
    target.mkdir()
    for path in (target, target / "report.txt"):
        if path != target:
            path.write_text("report")
        locator = path.as_uri() if use_uri else str(path)
        resource = LocatedResource(provider, locator)
        expected = MaterializedResource(path)
        assert resource.materialize() == expected
        assert resource.materialize(tmp_path / "unused-cache") == expected
        assert not (tmp_path / "unused-cache").exists()
    localhost = target.as_uri().replace("file:///", "file://localhost/")
    assert LocatedResource(provider, localhost).materialize().path == target


@pytest.mark.parametrize("locator", ["missing", "nfs://server/export", "file://server/export"])
def test_unavailable_local_paths(tmp_path: Path, locator: str) -> None:
    if locator == "missing":
        locator = str(tmp_path / "missing")
    with pytest.raises(MaterializationError):
        LocatedResource("nfs", locator).materialize()
    with pytest.raises(MaterializationError, match="Unsupported filesystem provider"):
        LocatedResource("s3", "s3://bucket/report").materialize(tmp_path)


def test_optional_git_dependencies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "dulwich.repo", None)
    resource = GitResource(str(tmp_path / "source"), "main", "a" * 40)
    with pytest.raises(MaterializationError, match=r"hwcatalog\[git\]"):
        resource.materialize(tmp_path / "cache")
