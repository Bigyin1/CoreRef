"""Exercise the shipped test helpers through the public SDK."""

from pathlib import Path

import pytest
from dulwich.repo import Repo

from coreref.api.graph import Graph
from coreref.api.records import LocalRecord, Ref
from coreref.testing import LocalServer, Sandbox, git_repository

pytestmark = pytest.mark.conan_remote


def test_packages_roundtrip_between_independent_clients(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    sandbox = Sandbox(conan_server, tmp_path / "sandbox")
    publisher, reader = sandbox.catalog(), sandbox.catalog()
    assert publisher.cache_directory != reader.cache_directory
    util = publisher.publish(sandbox.package(Ref("test.util.rtl.default/1.0")))
    local = sandbox.package(
        Ref("test.cpu.rtl.default/1.0"),
        sources={"rtl/cpu.sv": "module cpu; endmodule\n"},
        files={"nested/result.bin": b"\x00\x01"},
    )
    local.payload = {"custom": "flow data"}
    published = publisher.publish(local, dependencies=(util.ref,))
    graph = Graph(reader, published.ref)
    installed = graph.get_with_sources()
    cpu = next(record for record in installed if record.ref == published.ref)
    assert cpu.source is not None
    assert (cpu.source / "rtl/cpu.sv").read_text() == "module cpu; endmodule\n"
    assert cpu.files["nested/result.bin"].read_bytes() == b"\x00\x01"
    assert cpu.payload == local.payload
    assert {record.ref for record in installed} == {util.ref, published.ref}


def test_native_workspace_uses_fixture_sources(
    conan_server: LocalServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = Sandbox(conan_server, tmp_path / "sandbox")
    catalog = sandbox.catalog()
    workspace = sandbox.workspace(catalog)
    cpu = sandbox.package(Ref("test.cpu.rtl.default/1.0"), sources={"cpu.sv": "local RTL"})
    assert cpu.source is not None
    workspace.add(cpu.source, record=cpu)
    monkeypatch.chdir(workspace.path.parent)
    graph = Graph(catalog, LocalRecord(Ref("test.top.rtl.default/1.0")), dependencies=(cpu.ref,))
    editable = graph.get_with_sources()[0]
    assert editable.is_editable and editable.source == cpu.source
    assert (editable.source / "cpu.sv").read_text() == "local RTL"
    workspace.remove(cpu.source)
    assert workspace.members == {}


def test_repeated_package_inputs_do_not_overwrite_each_other(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    sandbox = Sandbox(conan_server, tmp_path)
    ref = Ref("test.cpu.rtl.default/1.0")
    first = sandbox.package(ref, files={"result": "first"})
    second = sandbox.package(ref, files={"result": "second"})
    assert first.files["result"] != second.files["result"]
    assert first.files["result"].read_text() == "first"


def test_git_helper_preserves_existing_directory_and_commits_sources(tmp_path: Path) -> None:
    directory = git_repository(tmp_path / "source", {"nested/cpu.sv": "RTL"})
    with Repo(directory) as repo:
        assert repo.head()
    with pytest.raises(FileExistsError):
        git_repository(directory, {"nested/cpu.sv": "replacement"})
    assert (directory / "nested/cpu.sv").read_text() == "RTL"


@pytest.mark.parametrize("name", ["../escape", "/absolute"])
def test_fixture_files_stay_in_owned_directory(
    conan_server: LocalServer, tmp_path: Path, name: str
) -> None:
    sandbox = Sandbox(conan_server, tmp_path)
    with pytest.raises(ValueError, match="inside its directory"):
        sandbox.package(Ref("test.cpu.rtl.default/1.0"), files={name: "bad"})
