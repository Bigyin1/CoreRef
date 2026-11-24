"""Describe build routes and retrieve their published artifacts.

Use ``StageSpec`` for declarative dependencies, ``Stage`` for the
flow's implementation and ``Route`` to validate the stage graph.
``Outputs`` retrieves named results from the Nix store.
"""

from .outputs import Outputs
from .route import Route, Stage, StageSpec, StageTransform

__all__ = ["Outputs", "Route", "Stage", "StageSpec", "StageTransform"]
