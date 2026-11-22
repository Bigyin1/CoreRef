"""Run local build stages in dependency order and reuse current results."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from doit.cmd_base import ModuleTaskLoader  # type: ignore[import-untyped]
from doit.doit_cmd import DoitMain  # type: ignore[import-untyped]
from doit.tools import config_changed  # type: ignore[import-untyped]

from ..api.records import JSON


@dataclass(frozen=True, kw_only=True)
class BuildStage:
    """Declare one action, its input files, and its outputs in the build directory."""

    name: str
    action: Callable[[], None]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    configuration: Mapping[str, JSON] = field(default_factory=dict)


@dataclass(frozen=True)
class Pipeline:
    """Run stages with dependencies inferred from their input and output paths."""

    directory: Path
    stages: tuple[BuildStage, ...]

    def run(self, target: str) -> None:
        """Bring a stage and its prerequisites up to date in this directory."""
        if not any(stage.name == target for stage in self.stages):
            raise ValueError(f"Unknown stage {target!r}")
        self.directory.mkdir(parents=True, exist_ok=True)
        loader = ModuleTaskLoader({"task_flow": self._tasks})
        runner = DoitMain(loader, config_filenames=())
        state = self.directory / "doit.db"
        if runner.run(["run", "--db-file", str(state), f"flow:{target}"]):
            raise RuntimeError(f"Build stage failed. See the output in {self.directory}.")

    def _tasks(self) -> Iterator[dict[str, object]]:
        for stage in self.stages:
            yield {
                "name": stage.name,
                "actions": [(self._execute, [stage])],
                "file_dep": [self.directory / name for name in stage.inputs],
                "targets": [self.directory / name for name in stage.outputs],
                "uptodate": [config_changed(dict(stage.configuration))],
            }

    def _execute(self, stage: BuildStage) -> None:
        stage.action()
        missing = [name for name in stage.outputs if not (self.directory / name).is_file()]
        if missing:
            raise RuntimeError(f"Stage {stage.name} did not produce {', '.join(missing)}")
