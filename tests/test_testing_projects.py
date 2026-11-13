"""Ordinary projects alongside this test are the fixture inputs."""

import json
import shutil
from pathlib import Path

import pytest
from dulwich.objects import Commit, Tree
from dulwich.repo import Repo

from coreref.api.records import LocalRecord, Ref
from coreref.testing import LocalServer, Sandbox, environment, git_repository
from coreref.tools.manifest import ManifestFile

PROJECTS = Path(__file__).with_name("test_projects")
pytestmark = pytest.mark.conan_remote


def test_soc_cpu_stdlib_from_ordinary_directories(tmp_path: Path) -> None:
    with environment(tmp_path / "infra", projects=PROJECTS, repositories=("stdlib", "cpu")) as env:
        publisher = env.catalog
        published = {}
        for name in ("stdlib", "cpu"):
            project = ManifestFile(env.projects / name / "manifest.yml")
            record = project.record
            record.source = project.path.parent
            published[name] = publisher.publish(record, dependencies=project.dependencies)
        reader = env.new_catalog()
        soc = ManifestFile(env.projects / "soc/manifest.yml")
        graph = soc.resolve(reader)
        records = {record.ref.component: record for record in graph.get_with_sources()}
        assert set(records) == {"cpu", "stdlib"}
        assert records["cpu"].source is not None
        assert (records["cpu"].source / "scripts/run.sh").stat().st_mode & 0o111
        assert (records["cpu"].source / "rtl/cpu.sv").is_file()
        assert not (env.projects / "soc/.git").exists()
        assert not (env.projects / "physical/.git").exists()
        assert not (PROJECTS / "cpu/.git").exists()
        # The server records SCM pointing at independent bare origins.
        shutil.rmtree(env.projects / "cpu")
        assert env.new_catalog().get_with_sources(published["cpu"].ref).source is not None


def test_project_copy_preserves_custom_manifest_and_can_be_editable(
    conan_server: LocalServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = Sandbox(conan_server, tmp_path / "infra")
    projects = env.load_projects(PROJECTS)
    catalog = env.catalog
    manifest = json.loads((projects / "physical/project.json").read_text())
    record = LocalRecord(
        Ref(manifest["ref"]),
        files={name: projects / "physical" / path for name, path in manifest["files"].items()},
    )
    published = catalog.publish(record)
    assert env.new_catalog().get(published.ref).files["timing"].read_text() == "library(cpu) {}\n"
    workspace = env.workspace(directory=projects)
    for name in ("stdlib", "cpu"):
        ManifestFile(projects / name / "manifest.yml").add_to_workspace(workspace)
    monkeypatch.chdir(projects / "soc")
    records = ManifestFile(projects / "soc/manifest.yml").resolve(catalog).get_with_sources()
    assert all(record.is_editable for record in records)
    assert {record.source for record in records} == {projects / "stdlib", projects / "cpu"}
    assert not (projects / "stdlib/.git").exists()


def test_copies_are_independent_and_destination_is_never_merged(
    conan_server: LocalServer,
    tmp_path: Path,
) -> None:
    first = Sandbox(conan_server, tmp_path / "one")
    second = Sandbox(conan_server, tmp_path / "two")
    a = first.load_projects(PROJECTS)
    b = second.load_projects(PROJECTS)
    (a / "cpu/rtl/cpu.sv").write_text("modified")
    assert (b / "cpu/rtl/cpu.sv").read_bytes() == (PROJECTS / "cpu/rtl/cpu.sv").read_bytes()
    with pytest.raises(FileExistsError):
        first.load_projects(PROJECTS)
    assert (a / "cpu/rtl/cpu.sv").read_text() == "modified"


def test_git_repository_can_commit_a_directory_without_inline_file_definitions(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    shutil.copytree(PROJECTS / "stdlib", checkout)
    (checkout / "scratch").mkdir()
    (checkout / "scratch/ignored").write_text("not a source")
    git_repository(checkout)
    with Repo(checkout) as repo:
        commit = repo[repo.head()]
        assert isinstance(commit, Commit)
        tree = repo[commit.tree]
        assert isinstance(tree, Tree)
        assert b"manifest.yml" in tree
        assert b"scratch" not in tree


@pytest.mark.parametrize("name", ["missing", "../outside", "/absolute"])
def test_invalid_repository_selection_does_not_copy_anything(
    conan_server: LocalServer,
    tmp_path: Path,
    name: str,
) -> None:
    env = Sandbox(conan_server, tmp_path / "infra")
    with pytest.raises((FileNotFoundError, ValueError)):
        env.load_projects(PROJECTS, repositories=(name,))
    assert not env.projects.exists()


def test_repository_selection_requires_projects_before_starting_server(tmp_path: Path) -> None:
    with (
        pytest.raises(ValueError, match="require a project tree"),
        environment(tmp_path, repositories=("cpu",)),
    ):
        pytest.fail("Invalid environment must not start")
    assert not (tmp_path / "server").exists()


def test_copying_source_into_itself_is_rejected(
    conan_server: LocalServer,
    tmp_path: Path,
) -> None:
    env = Sandbox(conan_server, tmp_path / "inside")
    with pytest.raises(ValueError, match="inside the source tree"):
        env.load_projects(tmp_path)
    assert not env.projects.exists()


def test_existing_git_metadata_is_not_copied_and_input_stays_unchanged(
    conan_server: LocalServer,
    tmp_path: Path,
) -> None:
    source = tmp_path / "input"
    git_repository(source / "cpu", {"cpu.sv": "original"})
    with Repo(source / "cpu") as repo:
        before = repo.head()
    env = Sandbox(conan_server, tmp_path / "infra")
    copied = env.load_projects(source)
    assert not (copied / "cpu/.git").exists()
    with Repo(source / "cpu") as repo:
        assert repo.head() == before


def test_absolute_repository_symlink_cannot_initialize_the_source_directory(
    conan_server: LocalServer,
    tmp_path: Path,
) -> None:
    source = tmp_path / "input"
    (source / "cpu").mkdir(parents=True)
    (source / "alias").symlink_to(source / "cpu", target_is_directory=True)
    env = Sandbox(conan_server, tmp_path / "infra")
    copied = env.load_projects(source, repositories=("alias",))
    assert (copied / "cpu/.git").exists()
    assert not (source / "cpu/.git").exists()


def test_inline_packages_and_projects_can_extend_a_directory_fixture(tmp_path: Path) -> None:
    with environment(tmp_path, projects=PROJECTS, repositories=("stdlib",)) as env:
        catalog = env.catalog
        stdlib = ManifestFile(env.projects / "stdlib/manifest.yml")
        record = stdlib.record
        record.source = stdlib.path.parent
        dependency = catalog.publish(record)
        generated = env.package(
            env.ref("generated"),
            sources={"generated.sv": "module generated; endmodule\n"},
            files={"report.txt": "inline result"},
        )
        published = catalog.publish(generated, dependencies=(dependency.ref,))
        consumer = env.project(env.ref("inline-soc"), dependencies=(published.ref,))
        installed = consumer.resolve(env.new_catalog()).get_with_sources()
        assert {record.ref.component for record in installed} == {"generated", "stdlib"}
        result = next(record for record in installed if record.ref == published.ref)
        assert result.files["report.txt"].read_text() == "inline result"
        assert result.source is not None and (result.source / "generated.sv").is_file()
        assert (env.projects / "cpu/rtl/cpu.sv").exists()
