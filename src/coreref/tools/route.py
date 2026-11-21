"""Compose project-specific build stages and execute them with doit."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import networkx as nx

Action = Callable[[], None]


class RouteError(RuntimeError):
    """An invalid route or a failed build stage."""


@dataclass(frozen=True)
class _Stage:
    action: Action
    after: tuple[str, ...]
    outputs: dict[str, Path]

    def run(self) -> None:
        self.action()
        for name, path in self.outputs.items():
            if not path.is_file():
                raise RouteError(f"Missing output {name!r} at {path}")


class Route:
    """Register the stages available in a project and collect their outputs."""

    def __init__(self) -> None:
        self._stages: dict[str, _Stage] = {}

    def stage(
        self,
        name: str,
        *,
        action: Action,
        after: tuple[str, ...] = (),
        outputs: Mapping[str, Path] | None = None,
    ) -> None:
        """Declare a stage and the files it produces when its action succeeds."""
        if not name.isidentifier():
            raise RouteError(f"Invalid stage name {name!r}")
        if name in self._stages:
            raise RouteError(f"Duplicate stage {name!r}")
        paths = {key: path.absolute() for key, path in (outputs or {}).items()}
        self._stages[name] = _Stage(action, after, paths)

    def run(self, target: str | None = None) -> dict[str, Path]:
        """Run the registered route, or just the named target and its predecessors."""
        names = self._order(target)
        outputs: dict[str, Path] = {}
        for name in names:
            for key, path in self._stages[name].outputs.items():
                if key in outputs:
                    raise RouteError(f"Duplicate route output {key!r}")
                outputs[key] = path
        if names:
            self._execute(names)
        return outputs

    def _order(self, target: str | None) -> tuple[str, ...]:
        graph: nx.DiGraph[str] = nx.DiGraph()
        graph.add_nodes_from(self._stages)
        for name, stage in self._stages.items():
            unknown = set(stage.after) - self._stages.keys()
            if unknown:
                raise RouteError(f"Stage {name!r} requires unknown stages {sorted(unknown)}")
            graph.add_edges_from((dependency, name) for dependency in stage.after)
        if not nx.is_directed_acyclic_graph(graph):
            raise RouteError("Route contains a dependency cycle")
        if target is not None:
            if target not in self._stages:
                raise RouteError(f"Unknown target {target!r}")
            selected = nx.ancestors(graph, target) | {target}
            graph = graph.subgraph(selected)
        return tuple(nx.topological_sort(graph))

    def _execute(self, names: tuple[str, ...]) -> None:
        try:
            from doit.cmd_base import ModuleTaskLoader  # type: ignore[import-untyped]
            from doit.doit_cmd import DoitMain  # type: ignore[import-untyped]
        except ModuleNotFoundError as error:
            raise RouteError("Install coreref[route] to execute a route") from error

        def task_route() -> Iterator[dict[str, object]]:
            for name in names:
                yield {
                    "name": name,
                    "actions": [self._stages[name].run],
                    "task_dep": [f"route:{dep}" for dep in self._stages[name].after],
                }

        with TemporaryDirectory(prefix="coreref-doit-") as temporary:
            loader = ModuleTaskLoader({"task_route": task_route})
            command = DoitMain(loader, config_filenames=())
            result = command.run(["run", "--db-file", str(Path(temporary) / "state"), "route"])
            if result:
                raise RouteError("Route failed. See the doit task output for the failed stage.")
