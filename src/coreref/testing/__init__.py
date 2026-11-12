"""Optional integration-test helpers; install with ``coreref[testing]``.

Directories and lifetimes belong to the caller (for example pytest's tmp_path).
Importing this module never starts a server or changes ambient Conan settings.
Dulwich is loaded only when a Git operation is requested.
"""

from .configuration import configured_home
from .git import FixtureFile, git_commit, git_repository, git_upstream
from .operations import OperationTrace, trace_operations
from .sandbox import Sandbox, environment
from .server import LocalServer, local_server

__all__ = [
    "FixtureFile",
    "LocalServer",
    "OperationTrace",
    "Sandbox",
    "configured_home",
    "environment",
    "git_commit",
    "git_repository",
    "git_upstream",
    "local_server",
    "trace_operations",
]
