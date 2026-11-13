"""Downstream workflows need consumers, editable projects and incremental updates."""

from pathlib import Path
from unittest.mock import patch

import pytest
from dulwich.objects import Commit
from dulwich.repo import Repo

from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError
from coreref.api.graph import Graph
from coreref.testing import (
    FixtureFile,
    LocalServer,
    Sandbox,
    configured_home,
    git_commit,
    git_repository,
)

pytestmark = pytest.mark.conan_remote


def test_existing_source_incremental_publish_and_executable_update(
    conan_server: LocalServer,
    tmp_path: Path,
) -> None:
    env = Sandbox(conan_server, tmp_path / "fixtures", namespace="flow")
    catalog = env.new_catalog()
    util = env.publish(catalog, env.package(env.ref("util")))
    initial = env.package(env.ref("cpu"), sources={"run.sh": FixtureFile("first", 0o755)})
    assert initial.source is not None
    record = env.package(env.ref("cpu"), source=initial.source)
    first = env.publish(catalog, record, dependencies=(util.ref,))
    git_commit(
        initial.source,
        {"run.sh": FixtureFile("second", 0o755)},
        push=True,
        author="Flow <flow@example.invalid>",
    )
    second = env.publish(catalog, record, dependencies=(util.ref,))
    assert first.ref != second.ref
    reader = env.new_catalog()
    for published, content in ((first, "first"), (second, "second")):
        installed = reader.get_with_sources(published.ref)
        assert installed.source is not None
        assert (installed.source / "run.sh").read_text() == content
        assert (installed.source / "run.sh").stat().st_mode & 0o111
    with Repo(initial.source) as repo:
        commit = repo[repo.head()]
        assert isinstance(commit, Commit)
        assert commit.author == b"Flow <flow@example.invalid>"


def test_package_sets_accept_previously_published_refs(
    conan_server: LocalServer,
    tmp_path: Path,
) -> None:
    env = Sandbox(conan_server, tmp_path)
    catalog = env.new_catalog()
    util = env.publish(catalog, env.package(env.ref("util")))
    records = {name: env.package(env.ref(name)) for name in ("cpu", "top")}
    result = env.publish_set(catalog, records, dependencies={"cpu": (util.ref,), "top": ("cpu",)})
    project = env.project(env.ref("consumer"), dependencies=(result["top"].ref,))
    assert {r.ref.component for r in project.resolve(env.new_catalog()).records} == {
        "consumer",
        "top",
        "cpu",
        "util",
    }


def test_consumer_deps_edit_lock_and_workspace_next_to_project(
    conan_server: LocalServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = Sandbox(conan_server, tmp_path / "fixtures")
    catalog = env.new_catalog()
    cpu = env.package(env.ref("cpu"), sources={"cpu.sv": "RTL"})
    published = catalog.publish(cpu)
    project = env.project(env.ref("top"), path=tmp_path / "project/manifest.yml")
    project.add(published.ref.without_revision())
    project.save()
    assert project.resolve(catalog).get_with_sources()[0].source != cpu.source
    project.create_lockfile(catalog)
    assert project.lockfile.is_file()
    project.lockfile.unlink()
    workspace = env.workspace(catalog, directory=tmp_path)
    assert cpu.source is not None
    workspace.add(cpu.source, record=cpu)
    monkeypatch.chdir(project.path.parent)
    editable = project.resolve(catalog).get_with_sources()[0]
    assert editable.source == cpu.source and editable.is_editable


def test_git_init_existing_directory_and_explicit_reuse(tmp_path: Path) -> None:
    directory = tmp_path / "checkout"
    directory.mkdir()
    (directory / "existing").write_text("preserve")
    git_repository(directory, {"cpu.sv": "first"})
    with Repo(directory) as repo:
        first = repo.head()
    git_repository(directory, {"cpu.sv": "second"}, reuse=True)
    with Repo(directory) as repo:
        commit = repo[repo.head()]
        assert isinstance(commit, Commit)
        assert commit.parents == [first]
    assert (directory / "existing").read_text() == "preserve"


def test_local_server_defaults_allow_local_git_sources(
    conan_server: LocalServer,
    tmp_path: Path,
) -> None:
    env = Sandbox(conan_server, tmp_path / "fixtures")
    catalog = Catalog(conan_server.configure(tmp_path / "client"))
    published = catalog.publish(env.package(env.ref("cpu"), sources={"cpu.sv": "RTL"}))
    assert catalog.get_with_sources(published.ref).source is not None


def test_profile_uses_native_detection_and_preserves_existing_profile(tmp_path: Path) -> None:
    with patch(
        "conan.internal.api.profile.detect.detect_defaults_settings",
        return_value=[
            ("os", "Linux"),
            ("arch", "armv8"),
            ("build_type", "Release"),
        ],
    ) as detect:
        home = configured_home(tmp_path / "client")
        assert "arch=armv8" in (home / "profiles/default").read_text()
        (home / "profiles/default").write_text("custom profile")
        configured_home(home)
        assert (home / "profiles/default").read_text() == "custom profile"
        assert detect.call_count == 1


def test_fixture_ref_coordinates_and_invalid_source_options(
    conan_server: LocalServer,
    tmp_path: Path,
) -> None:
    env = Sandbox(conan_server, tmp_path, namespace="soc")
    assert (
        str(env.ref("cpu_core", "2.0", view="be", type="lib-lef")) == "soc.cpu_core.be.lib-lef/2.0"
    )
    with pytest.raises(ValueError, match="existing directory"):
        env.package(env.ref("cpu"), source=tmp_path, sources={})
    with pytest.raises(FileNotFoundError):
        env.package(env.ref("cpu"), source=tmp_path / "missing")


def test_single_publication_keeps_lock_choice_and_records_only_successes(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    env = Sandbox(conan_server, tmp_path / "fixtures")
    catalog = env.new_catalog()
    dependency = env.package(env.ref("cpu"))
    first = env.publish(catalog, dependency)
    consumer = env.project(env.ref("top"), dependencies=(dependency.ref,))
    lock = consumer.create_lockfile(catalog)
    dependency.payload = {"generation": 2}
    second = env.publish(catalog, dependency)
    assert first.ref != second.ref
    locked = env.publish(
        catalog, consumer.record, dependencies=consumer.dependencies, lockfile=lock
    )
    reader = env.new_catalog()
    assert Graph(reader, locked.ref).requirements == (first.ref,)
    assert env.publish(catalog, consumer.resolve(catalog)).ref == locked.ref
    with pytest.raises(CatalogError):
        env.publish(catalog, env.package(env.ref("broken")), dependencies=(env.ref("absent"),))
    diagnostic = env.diagnostics()
    assert str(first.ref) in diagnostic and str(second.ref) in diagnostic
    assert "Published: test.broken." not in diagnostic
