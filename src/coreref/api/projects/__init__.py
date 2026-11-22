"""Work with project configuration, dependency snapshots and editable checkouts.

Load a ``Manifest`` and bind it to a checkout through ``Project``.
Use ``Graph`` to inspect and fetch resolved dependencies. ``Workspace``
registers working checkouts for Conan to use as editables.
"""

from .graph import Graph
from .manifest import Manifest
from .project import Project
from .workspace import Workspace

__all__ = ["Graph", "Manifest", "Project", "Workspace"]
