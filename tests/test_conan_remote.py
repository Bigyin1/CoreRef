"""Round trips through an owned Conan server, never a configured user server."""

import secrets
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from conan.api.conan_api import ConanAPI  # type: ignore[import-untyped]
from conan.cli.cli import Cli  # type: ignore[import-untyped]

from examples.conan_config import configured_home
from hwcatalog._internal.conan import graph as conan_graph
from hwcatalog._internal.conan.format import read_recipe, write_recipe
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import (
    CatalogError,
    Conflict,
)
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import Record, Ref
from tests.helpers import resolve_published

pytestmark = pytest.mark.conan_remote


@pytest.fixture
def remote_catalog(tmp_path: Path) -> Iterator[Catalog]:
    server_executable = Path(sys.executable).parent / "conan_server"
    if not server_executable.is_file():
        pytest.skip("Install Conan and conan-server development dependencies")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    folder = tmp_path / "server"
    folder.mkdir()
    password = secrets.token_hex(16)
    config = (
        "[server]\n"
        f"jwt_secret: {secrets.token_hex(32)}\n"
        "jwt_expire_minutes: 120\nssl_enabled: False\n"
        f"port: {port}\npublic_port: {port}\nhost_name: 127.0.0.1\n"
        "authorize_timeout: 1800\ndisk_authorize_timeout: 1800\n"
        f"updown_secret: {secrets.token_hex(32)}\n"
        f"disk_storage_path: {folder / 'data'}\n"
        "[write_permissions]\n*/*@*/*: test\n"
        "[read_permissions]\n*/*@*/*: *\n"
        f"[users]\ntest: {password}\n"
    )
    (folder / "server.conf").write_text(config)
    with (folder / "server.log").open("w+") as log:
        process = subprocess.Popen(  # noqa: S603 - owned server, isolated config
            [str(server_executable), "--server_dir", str(folder)], stdout=log, stderr=log, text=True
        )
        try:
            _wait_for_server(f"http://127.0.0.1:{port}/v2/ping", process, log.name)
            home = configured_home(tmp_path / "publisher")
            api = ConanAPI(cache_folder=str(home))
            Cli(api).add_commands()
            api.command.run(["remote", "add", "catalog", f"http://127.0.0.1:{port}", "-vquiet"])
            api.command.run(
                ["remote", "login", "catalog", "test", "--password", password, "-vquiet"]
            )
            connection = Catalog(home)
            yield connection
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def _wait_for_server(url: str, process: subprocess.Popen[str], log: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=0.5) as response:  # noqa: S310 - owned loopback URL
                if response.status == 200:
                    return
        except URLError, TimeoutError:
            if process.poll() is not None:
                break
            time.sleep(0.05)
    pytest.fail(f"Temporary Conan server did not become ready: {Path(log).read_text()}")


def test_remote_round_trip_external_ci_and_cache_independence(
    remote_catalog: Catalog, tmp_path: Path
) -> None:
    rtl = Record(Ref("soc", "cpu", "rtl", "default", "1.0"), {"source": "cpu.sv"})
    be = Record(Ref("soc", "cpu", "be", "lib-lef", "4.0"), {"source": "cpu.lib"})
    catalog = remote_catalog
    catalog.publish(rtl)
    catalog.publish(be, dependencies=(rtl.ref,))
    assert (
        Ref(**rtl.ref.to_dict(), catalog=catalog)
        .find_dependents(candidates=SearchQuery(type="lib-lef", view="be"))[0]
        .ref
        == be.ref
    )
    with pytest.raises(Conflict):
        catalog.publish(rtl)
    original_graph = resolve_published(catalog, be.ref)
    native, _ = conan_graph.load_graph(remote_catalog._client, be.ref)
    native_refs = tuple(
        str(dep.ref) for dep in native.root.conanfile.dependencies.host.topological_sort.values()
    )
    assert native_refs == ("soc.cpu.rtl.default/1.0",)
    # A conflicting local-only revision must never enter published requirements.
    rogue = tmp_path / "local-only-revision"
    write_recipe(rogue, Record(rtl.ref, {"local-only": True}))
    remote_catalog._client.create_lockfile(rogue)
    remote_catalog._client.export(rogue)
    another_be = Record(Ref("soc", "cpu", "be", "lib-lef", "5.0"))
    catalog.publish(another_be, dependencies=(rtl.ref,))
    assert {r.ref for r in resolve_published(catalog, another_be.ref).records} == {
        another_be.ref,
        rtl.ref,
    }
    home = tmp_path / "reader"
    home.mkdir()
    shutil.copyfile(remote_catalog._client.home / "remotes.json", home / "remotes.json")
    reader = Catalog(configured_home(home), remote="catalog")
    assert Ref(**be.ref.to_dict(), catalog=reader).get().payload == {"source": "cpu.lib"}
    assert resolve_published(reader, be.ref) == original_graph
    new_rtl = Record(Ref("soc", "cpu", "rtl", "default", "2.0"))
    external = tmp_path / "ci"
    write_recipe(external, new_rtl)
    remote_catalog._client.create_lockfile(external)
    pinned = remote_catalog._client.export(external)
    remote_catalog._client.upload(pinned)
    assert Ref(**new_rtl.ref.to_dict(), catalog=reader).get().ref == new_rtl.ref
    assert resolve_published(reader, be.ref) == original_graph
    remote_catalog._client.run("remote", "update", "catalog", "--url", "http://127.0.0.1:1")
    with pytest.raises(CatalogError):
        Ref(**rtl.ref.to_dict(), catalog=catalog).find_dependents(
            candidates=SearchQuery(type="lib-lef", view="be")
        )


def test_cached_package_is_not_published_until_remote_upload_succeeds(
    remote_catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = Record(Ref("soc", "cpu", "rtl", "default", "1.0"), {"source": "cpu.sv"})
    catalog = remote_catalog

    def reject_upload(reference: str) -> None:
        raise CatalogError(f"Simulated upload failure: {reference}")

    with monkeypatch.context() as patch:
        patch.setattr(remote_catalog._client, "upload", reject_upload)
        with pytest.raises(CatalogError, match="Simulated upload failure"):
            catalog.publish(record)
    directory = remote_catalog._client.cache_path(str(record.ref), download=False)
    assert read_recipe(directory, record.ref)[0] == record
    assert catalog.search() == ()

    # A prepared local package must not block another attempt to publish to the server.
    catalog.publish(record)
    assert Ref(**record.ref.to_dict(), catalog=catalog).get() == record


def test_remote_keeps_artifacts_and_fetches_sources_from_git_on_demand(
    remote_catalog: Catalog, tmp_path: Path, checkout: Path, git_origin: Path
) -> None:
    # The isolated test server deliberately accepts a local SCM URL.
    (remote_catalog._client.home / "global.conf").write_text("core.scm:local_url=allow\n")
    dependency = Record(Ref("tools", "flow", "run", "default", "1.0"))
    remote_catalog.publish(dependency)
    artifact = tmp_path / "cpu.lib"
    artifact.write_text("library(cpu) {}")
    published = remote_catalog.publish(
        Record(Ref("soc", "cpu", "be", "lib-lef", "1.0"), {"corner": "tt"}),
        dependencies=(dependency.ref,),
        source_path=checkout,
        files={"cpu.lib": artifact},
    )
    artifact.unlink()
    shutil.rmtree(checkout)
    home = tmp_path / "fresh-reader"
    home.mkdir()
    shutil.copyfile(remote_catalog._client.home / "remotes.json", home / "remotes.json")
    reader = Catalog(configured_home(home), remote="catalog")
    assert Ref(**published.ref.to_dict(), catalog=reader).get() == published
    installed = Ref(**published.ref.to_dict(), catalog=reader).materialize()
    assert installed.source_path is None
    assert not (installed.path / "source").exists()
    assert (installed.path / "artifacts/cpu.lib").read_text() == "library(cpu) {}"
    assert published.source is not None
    assert published.source.repository_url == str(git_origin)
    offline = git_origin.with_name("offline-origin.git")
    git_origin.rename(offline)
    try:
        with pytest.raises(CatalogError):
            Ref(**published.ref.to_dict(), catalog=reader).materialize(with_sources=True)
    finally:
        offline.rename(git_origin)
    fetched = Ref(**published.ref.to_dict(), catalog=reader).materialize(with_sources=True)
    assert fetched.path == installed.path
    assert fetched.source_path is not None
    assert (fetched.source_path / "cpu.sv").read_text() == "module cpu; endmodule\n"
    assert not fetched.source_path.is_relative_to(fetched.path)


def test_one_configured_remote_is_used_automatically(
    remote_catalog: Catalog, tmp_path: Path
) -> None:
    record = Record(Ref("soc", "automatic", "rtl", "default", "1.0"))
    publisher = remote_catalog
    assert remote_catalog._client.remote == "catalog"
    publisher.publish(record)
    home = configured_home(tmp_path / "automatic-reader")
    shutil.copyfile(remote_catalog._client.home / "remotes.json", home / "remotes.json")
    reader = Catalog(home)
    assert Ref(**record.ref.to_dict(), catalog=reader).get() == record
