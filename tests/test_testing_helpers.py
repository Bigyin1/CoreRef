"""Exercise the shipped test helpers through the public SDK."""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from coreref.api.graph import Graph
from coreref.api.records import LocalRecord, Ref
from coreref.testing import LocalServer, Sandbox, environment, git_repository, git_upstream

pytestmark = pytest.mark.conan_remote


def test_packages_roundtrip_between_independent_clients(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    sandbox = Sandbox(conan_server, tmp_path / "sandbox")
    publisher, reader = sandbox.new_catalog(), sandbox.new_catalog()
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
    catalog = sandbox.new_catalog()
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


def test_package_hierarchy_downloads_from_bare_upstreams(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    sandbox = Sandbox(conan_server, tmp_path)
    publisher = sandbox.new_catalog()
    links = {"util": (), "cpu": ("util",), "io": ("util",), "top": ("cpu", "io")}
    records = {
        name: sandbox.package(
            Ref(f"test.{name}.rtl.default/1.0"),
            sources={f"{name}.sv": f"module {name}; endmodule\n"},
        )
        for name in links
    }
    # Input order need not follow the dependency order.
    published = sandbox.publish_set(
        publisher, dict(reversed(tuple(records.items()))), dependencies=links
    )
    # Remove every publisher checkout: SCM retrieval must use its bare origin.
    for local in records.values():
        assert local.source is not None
        shutil.rmtree(local.source)
    graph = Graph(sandbox.new_catalog(), published["top"].ref)
    assert {(a.component, b.component) for a, b in graph.networkx.edges} == {
        (name, dependency) for name, dependencies in links.items() for dependency in dependencies
    }
    installed = graph.get_with_sources()
    assert len(installed) == len(records)
    for record in installed:
        assert record.source is not None
        assert (record.source / f"{record.ref.component}.sv").is_file()
        with Repo(record.source) as repo:
            origin = Path(repo.get_config().get((b"remote", b"origin"), b"url").decode())
        assert origin.name == "upstream.git" and origin.is_dir()


def test_editable_hierarchy_can_mix_local_and_published_packages(
    conan_server: LocalServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = Sandbox(conan_server, tmp_path)
    catalog = sandbox.new_catalog()
    util = catalog.publish(sandbox.package(Ref("test.util.rtl.default/1.0")))
    cpu = sandbox.package(Ref("test.cpu.rtl.default/1.0"), sources={"cpu.sv": "CPU"})
    top = sandbox.package(Ref("test.top.rtl.default/1.0"), sources={"top.sv": "TOP"})
    workspace = sandbox.workspace(catalog)
    assert cpu.source is not None and top.source is not None
    workspace.add(cpu.source, record=cpu, dependencies=(util.ref,))
    workspace.add(top.source, record=top, dependencies=(cpu.ref, util.ref))
    monkeypatch.chdir(workspace.path.parent)
    graph = Graph(catalog, top.ref)
    installed = {record.ref.component: record for record in graph.get_with_sources()}
    assert installed["cpu"].source == cpu.source and installed["cpu"].is_editable
    assert installed["top"].source == top.source and installed["top"].is_editable
    assert not installed["util"].is_editable


def test_bare_upstream_supports_later_pushes_and_preserves_existing_directory(
    tmp_path: Path,
) -> None:
    checkout = git_repository(tmp_path / "source", {"cpu.sv": "first"})
    upstream = git_upstream(checkout, tmp_path / "origin.git")
    with pytest.raises(FileExistsError):
        git_upstream(checkout, upstream)
    (checkout / "cpu.sv").write_text("second")
    porcelain.add(checkout, paths=["cpu.sv"])
    porcelain.commit(
        checkout,
        message=b"Update",
        author=b"Test <test@example.invalid>",
        committer=b"Test <test@example.invalid>",
        sign=False,
    )
    porcelain.push(checkout, str(upstream), refspecs="refs/heads/main")
    with Repo(checkout) as source, Repo(upstream) as remote:
        assert source.head() == remote.head()


@pytest.mark.parametrize(
    "dependencies",
    [
        {"cpu": ("missing",)},
        {"missing": ()},
        {"cpu": ("cpu",)},
        {"cpu": ("top",), "top": ("cpu",)},
    ],
)
def test_package_set_rejects_invalid_links_before_publication(
    conan_server: LocalServer,
    tmp_path: Path,
    dependencies: dict[str, tuple[str, ...]],
) -> None:
    sandbox = Sandbox(conan_server, tmp_path)
    catalog = sandbox.new_catalog()
    records = {
        name: sandbox.package(Ref(f"test.{name}.rtl.default/1.0")) for name in ("cpu", "top")
    }
    with patch.object(catalog, "publish") as publish:
        with pytest.raises(ValueError, match=r"Unknown fixture packages|contain a cycle"):
            sandbox.publish_set(catalog, records, dependencies=dependencies)
        publish.assert_not_called()


def test_package_set_can_be_empty(conan_server: LocalServer, tmp_path: Path) -> None:
    sandbox = Sandbox(conan_server, tmp_path)
    assert sandbox.publish_set(sandbox.new_catalog(), {}, dependencies={}) == {}


def test_environment_owns_server_clients_and_git_origins(tmp_path: Path) -> None:
    with environment(tmp_path) as sandbox:
        publisher = sandbox.new_catalog()
        records = {
            name: sandbox.package(Ref(f"test.{name}.rtl.default/1.0"), sources={f"{name}.sv": name})
            for name in ("cpu", "top")
        }
        published = sandbox.publish_set(publisher, records, dependencies={"top": ("cpu",)})
        graph = Graph(sandbox.new_catalog(), published["top"].ref)
        assert len(graph.get_with_sources()) == 2
        assert sandbox.server.url.startswith("http://127.0.0.1:")
    assert (tmp_path / "server/server.log").is_file()
