"""Owned local Conan server for development examples and integration tests.

All server state and credentials belong to a temporary directory; this module
is used only by development examples and tests.
"""

import secrets
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from conan.api.conan_api import ConanAPI  # type: ignore[import-untyped]
from conan.cli.cli import Cli  # type: ignore[import-untyped]

from examples.conan_config import configured_home


@dataclass(frozen=True)
class LocalServer:
    url: str
    password: str = field(repr=False)
    remote: str = "catalog-test"

    def configure(self, home: Path, *, local_sources: bool = False) -> Path:
        """Configure one owned test cache, without changing ambient CONAN_HOME."""
        configured_home(home)
        if local_sources:
            # Only this private test server accepts SCM URLs of temporary Git repos.
            (home / "global.conf").write_text("core.scm:local_url=allow\n")
        api = ConanAPI(cache_folder=str(home.resolve()))
        Cli(api).add_commands()
        api.command.run(["remote", "add", self.remote, self.url, "-vquiet"])
        api.command.run(
            [
                "remote",
                "login",
                self.remote,
                "test",
                "--password",
                self.password,
                "-vquiet",
                "-cc",
                "core:non_interactive=True",
            ]
        )

        return home


def _server_config(root: Path, port: int, password: str) -> str:
    return (
        "[server]\n"
        f"jwt_secret: {secrets.token_hex(32)}\njwt_expire_minutes: 120\nssl_enabled: False\n"
        f"port: {port}\npublic_port: {port}\nhost_name: 127.0.0.1\n"
        "authorize_timeout: 1800\ndisk_authorize_timeout: 1800\n"
        f"updown_secret: {secrets.token_hex(32)}\ndisk_storage_path: {root / 'data'}\n"
        "[write_permissions]\n*/*@*/*: test\n[read_permissions]\n*/*@*/*: *\n"
        f"[users]\ntest: {password}\n"
    )


def _wait_for_server(server: LocalServer, process: subprocess.Popen[str], log: Path) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urlopen(server.url + "/v2/ping", timeout=0.5) as response:  # noqa: S310 - loopback
                status = response.status
            if status == HTTPStatus.OK:
                return
        except URLError, TimeoutError:
            if process.poll() is not None:
                break
            time.sleep(0.05)
    raise RuntimeError(f"Conan test server did not become ready: {log.read_text()}")


@contextmanager
def local_server(root: Path) -> Iterator[LocalServer]:
    executable = Path(sys.executable).parent / "conan_server"
    if not executable.is_file():
        raise RuntimeError("Install development dependencies with uv sync --frozen")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    root.mkdir(parents=True)
    server = LocalServer(f"http://127.0.0.1:{port}", secrets.token_hex(16))
    (root / "server.conf").write_text(_server_config(root, port, server.password))
    log_path = root / "server.log"
    with log_path.open("w+") as log:
        process = subprocess.Popen(  # noqa: S603 - owned server and isolated configuration
            [str(executable), "--server_dir", str(root)], stdout=log, stderr=log, text=True
        )
        try:
            _wait_for_server(server, process, log_path)
            yield server
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
