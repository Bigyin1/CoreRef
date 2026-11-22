"""Run local build stages in dependency order and reuse current results."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from doit.cmd_base import ModuleTaskLoader  # type: ignore[import-untyped]
from doit.doit_cmd import DoitMain  # type: ignore[import-untyped]
from doit.tools import config_changed  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from ..api.build.route import BuildStage


@dataclass(frozen=True)
class Pipeline:
    """Run stages with dependencies inferred from their input and output paths."""

    directory: Path
    stages: tuple[BuildStage, ...]

    def run(self, target: str, *additional_targets: str) -> None:
        """Bring the requested stages and their prerequisites up to date."""
        targets = (target, *additional_targets)
        known = {stage.name for stage in self.stages}
        for name in targets:
            if name not in known:
                raise ValueError(f"Unknown stage {name!r}")
        self.directory.mkdir(parents=True, exist_ok=True)
        loader = ModuleTaskLoader({"task_flow": self._tasks})
        runner = DoitMain(loader, config_filenames=())
        state = self.directory / "doit.db"
        tasks = [f"flow:{name}" for name in targets]
        if runner.run(["run", "--db-file", str(state), *tasks]):
            raise RuntimeError(f"Build stage failed. See the output in {self.directory}.")

    def _tasks(self) -> Iterator[dict[str, object]]:
        for stage in self.stages:
            yield {
                "name": stage.name,
                "actions": [(self._execute, [stage])],
                "file_dep": [self.directory / name for name in stage.inputs],
                "targets": [self.directory / name for name in stage.outputs],
                "task_dep": [f"flow:{name}" for name in stage.needs],
                "uptodate": [False]
                if stage.always_run
                else [config_changed(dict(stage.configuration))],
            }

    def _execute(self, stage: BuildStage) -> None:
        stage.action()
        missing = [name for name in stage.outputs if not (self.directory / name).is_file()]
        if missing:
            raise RuntimeError(f"Stage {stage.name} did not produce {', '.join(missing)}")
