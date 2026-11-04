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

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import BackendUnavailable, IdentityConflict
from hwcatalog.api.models.graph import Dependency
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.backends.conan import ConanBackend, write_recipe

pytestmark = pytest.mark.conan_remote


@pytest.fixture
def remote_backend(tmp_path: Path) -> Iterator[ConanBackend]:
    executable = Path(sys.executable).parent / "conan"
    server_executable = Path(sys.executable).parent / "conan_server"
    if not executable.is_file() or not server_executable.is_file():
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
            backend = ConanBackend(
                tmp_path / "publisher", remote="catalog", executable=str(executable)
            )
            backend._cli.run("remote", "add", "catalog", f"http://127.0.0.1:{port}")
            backend._cli.run("remote", "login", "catalog", "test", "--password", password)
            yield backend
            backend.close()
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
    remote_backend: ConanBackend, tmp_path: Path
) -> None:
    rtl = Record(Ref("cpu", "1.0", "rtl", "soc"), {"source": "cpu.sv"})
    be = Record(Ref("cpu", "4.0", "lib-lef", "soc"), {"source": "cpu.lib"})
    catalog = Catalog(remote_backend)
    catalog.publish((be, rtl), dependencies=(Dependency(be.ref, rtl.ref),))
    assert catalog.find_dependents(rtl.ref, candidates=SearchQuery(kind="lib-lef"))[0].ref == be.ref
    with pytest.raises(IdentityConflict):
        catalog.publish((rtl,))
    original_graph = catalog.graph.dependency_closure(be.ref)
    native = remote_backend.load_graph(be.ref)
    native_refs = tuple(
        str(dep.ref) for dep in native.root.conanfile.dependencies.host.topological_sort.values()
    )
    assert native_refs == ("soc.cpu.rtl/1.0",)
    # A conflicting local-only revision must never leak into a remote publication's lockfile.
    rogue = tmp_path / "local-only-revision"
    write_recipe(rogue, Record(rtl.ref, {"local-only": True}))
    remote_backend._cli.create_lockfile(rogue)
    remote_backend._cli.export(rogue)
    another_be = Record(Ref("cpu", "5.0", "lib-lef", "soc"))
    catalog.publish((another_be,), dependencies=(Dependency(another_be.ref, rtl.ref),))
    assert {r.ref for r in catalog.graph.dependency_closure(another_be.ref).records} == {
        another_be.ref,
        rtl.ref,
    }
    home = tmp_path / "reader"
    home.mkdir()
    shutil.copyfile(remote_backend._cli.home / "remotes.json", home / "remotes.json")
    with Catalog(
        ConanBackend(home, remote="catalog", executable=remote_backend._cli.executable)
    ) as reader:
        assert reader.get(be.ref).payload == {"source": "cpu.lib"}
        assert reader.graph.dependency_closure(be.ref) == original_graph
        new_rtl = Record(Ref("cpu", "2.0", "rtl", "soc"))
        external = tmp_path / "ci"
        write_recipe(external, new_rtl)
        remote_backend._cli.create_lockfile(external)
        pinned = remote_backend._cli.export(external)
        remote_backend._cli.upload(pinned)
        assert reader.get(new_rtl.ref).ref == new_rtl.ref
        assert reader.graph.dependency_closure(be.ref) == original_graph
    remote_backend._cli.run("remote", "update", "catalog", "--url", "http://127.0.0.1:1")
    with pytest.raises(BackendUnavailable):
        catalog.find_dependents(rtl.ref, candidates=SearchQuery(kind="lib-lef"))
