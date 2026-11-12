"""Optional integration-test helpers; install with ``coreref[testing]``.

Directories and lifetimes belong to the caller (for example pytest's tmp_path).
Importing this module never starts a server or changes ambient Conan settings.
"""

from .configuration import configured_home
from .git import FixtureFile, git_commit, git_repository, git_upstream
from .sandbox import Sandbox, environment
from .server import LocalServer, local_server

__all__ = [
    "FixtureFile",
    "LocalServer",
    "Sandbox",
    "configured_home",
    "environment",
    "git_commit",
    "git_repository",
    "git_upstream",
    "local_server",
]
