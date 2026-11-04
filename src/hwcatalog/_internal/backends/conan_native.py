"""Version-specific boundary to Conan's native graph API.

Conan does not ship type annotations. Only this adapter imports its untyped APIs;
the returned graph remains the actual Conan DepsGraph object.
"""

from collections.abc import Mapping
from pathlib import Path
from threading import RLock
from typing import Protocol, cast

from conan.api.conan_api import ConanAPI  # type: ignore[import-untyped]
from conan.cli.cli import Cli  # type: ignore[import-untyped]
from conan.errors import ConanException  # type: ignore[import-untyped]
from conan.internal.graph.graph_error import (  # type: ignore[import-untyped]
    GraphConflictError,
    GraphLoopError,
)

from ...api.errors import BackendUnavailable, DependencyCycle, InvalidPackage, VersionConflict
from ...backends.conan_types import NativeGraph

_API_LOCK = RLock()


class _Commands(Protocol):
    def run(self, cmd: list[str], raise_on_errors: bool) -> Mapping[str, object]: ...


class _API(Protocol):
    command: _Commands


def load_native_graph(home: Path, arguments: list[str]) -> NativeGraph:
    """Run Conan's graph command in process and retain its native return object."""
    try:
        # Conan commands temporarily modify global output settings.
        with _API_LOCK:
            conan = ConanAPI(cache_folder=str(home))
            Cli(conan).add_commands()
            api = cast(_API, conan)
            result = api.command.run(arguments, raise_on_errors=False)
            graph = cast(NativeGraph, result["graph"])
            graph.report_graph_error()
            return graph
    except GraphConflictError as exc:
        raise VersionConflict(str(exc)) from exc
    except GraphLoopError as exc:
        raise DependencyCycle(str(exc)) from exc
    except ConanException as exc:
        # Conan 2.34 uses the base exception for incomplete lockfiles.
        if "not in lockfile" in str(exc):
            raise InvalidPackage("Recipe dependency is absent from its lockfile") from exc
        raise BackendUnavailable(f"Conan graph API failed: {exc}") from exc
    except OSError as exc:
        raise BackendUnavailable(f"Conan graph API failed: {exc}") from exc
