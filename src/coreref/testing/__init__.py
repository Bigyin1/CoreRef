"""Optional integration-test helpers; install with ``coreref[testing]``.

Directories and lifetimes belong to the caller (for example pytest's tmp_path).
Importing this module never starts a server or changes ambient Conan settings.
"""

from .configuration import configured_home
from .sandbox import Sandbox, git_repository
from .server import LocalServer, local_server

__all__ = ["LocalServer", "Sandbox", "configured_home", "git_repository", "local_server"]
