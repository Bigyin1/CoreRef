"""Server-only callers must not load the optional Git library."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.conan_remote
def test_server_helpers_work_when_dulwich_import_is_blocked(tmp_path: Path) -> None:
    code = """
import importlib.abc
import sys
from pathlib import Path

class BlockDulwich(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "dulwich" or fullname.startswith("dulwich."):
            raise AssertionError("Server helpers must not import Dulwich")

sys.meta_path.insert(0, BlockDulwich())
from coreref.api.records import Ref
from coreref.testing import configured_home, local_server, Sandbox
from coreref.testing.server import LocalServer

root = Path(sys.argv[1])
configured_home(root / "offline")
with local_server(root / "server") as server:
    assert isinstance(server, LocalServer)
    server.configure(root / "reader")
    Sandbox(server, root / "fixtures").package(
        Ref("test.meta.rtl.default/1.0")
    )
assert not any(name == "dulwich" or name.startswith("dulwich.") for name in sys.modules)
"""
    result = subprocess.run(  # noqa: S603 - fixed script with the current Python interpreter
        [sys.executable, "-I", "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
