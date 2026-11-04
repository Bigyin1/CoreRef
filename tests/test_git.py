"""Resolve real local Git objects without invoking a Git executable or network."""

import sys
from collections.abc import Iterator
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from dulwich.objects import Commit, ObjectID, Tag, Tree
from dulwich.refs import Ref
from dulwich.repo import Repo

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import GitResolutionError
from hwcatalog.api.models.resources import GitResource
from tests.helpers import release

URL = "https://example.invalid/project.git"


@pytest.fixture(params=[(False, "sha1"), (True, "sha1"), (False, "sha256"), (True, "sha256")])
def repository(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Repo]:
    bare, object_format = request.param
    initialize = Repo.init_bare if bare else Repo.init
    with initialize(tmp_path, object_format=object_format) as repo:
        tree = Tree()
        repo.object_store.add_object(tree)
        commit = Commit()
        commit.tree = tree.get_id(repo.object_store.object_format)
        commit.author = commit.committer = b"Tests <tests@example.invalid>"
        commit.author_time = commit.commit_time = 1
        commit.author_timezone = commit.commit_timezone = 0
        commit.message = b"Test commit"
        repo.object_store.add_object(commit)
        oid = ObjectID(commit.get_id(repo.object_store.object_format))
        repo.refs[Ref(b"refs/heads/main")] = oid
        repo.refs.set_symbolic_ref(Ref(b"HEAD"), Ref(b"refs/heads/main"))
        repo.refs[Ref(b"refs/tags/light")] = oid
        tag = Tag()
        tag.name = b"annotated"
        tag.object = (Commit, oid)
        tag.tagger = commit.author
        tag.tag_time, tag.tag_timezone = 1, 0
        tag.message = b"Annotated tag"
        repo.object_store.add_object(tag)
        repo.refs[Ref(b"refs/tags/annotated")] = ObjectID(
            tag.get_id(repo.object_store.object_format)
        )
        yield repo


def test_resolve_local_refs_without_modification(repository: Repo) -> None:
    root = Path(repository.path)
    (root / "untracked.txt").write_text("Local changes are not published")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    oid = repository.head().decode("ascii")
    for ref in ("HEAD", "main", "refs/heads/main", "light", "annotated", oid):
        resource = GitResource.from_repository(root, repository_url=URL, requested_ref=ref)
        assert resource == GitResource(URL, ref, oid)
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_missing_ref_and_noncommit(repository: Repo) -> None:
    commit = repository[repository.head()]
    assert isinstance(commit, Commit)
    for ref in ("not-a-ref", commit.tree.decode("ascii")):
        with pytest.raises(GitResolutionError, match="Cannot resolve"):
            GitResource.from_repository(repository.path, repository_url=URL, requested_ref=ref)


def test_resolved_resource_can_be_published(repository: Repo, catalog: Catalog) -> None:
    resource = GitResource.from_repository(
        repository.path, repository_url=URL, requested_ref="annotated"
    )
    record = replace(release("git"), payload=(asdict(resource),))
    catalog.publish((record,))
    assert catalog.get(record.ref) == record


def test_missing_and_empty_repository(tmp_path: Path) -> None:
    for root in (tmp_path, tmp_path / "missing"):
        with pytest.raises(GitResolutionError):
            GitResource.from_repository(root, repository_url=URL)
    with Repo.init(tmp_path), pytest.raises(GitResolutionError):
        GitResource.from_repository(str(tmp_path), repository_url=URL)


def test_git_extra_is_optional(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "dulwich.errors", None)
    with pytest.raises(GitResolutionError, match=r"hwcatalog\[git\]"):
        GitResource.from_repository(tmp_path, repository_url=URL)
