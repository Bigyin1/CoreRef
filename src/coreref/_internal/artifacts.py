"""Content-addressed files and exact links stored in CoreRef recipe metadata."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..api.artifacts import file_digest
from ..api.catalog import Catalog
from ..api.errors import NotFound
from ..api.records import JSON, LocalRecord, Record, Ref

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    ref: str
    sha256: Digest
    size: int = Field(ge=0)

    @model_validator(mode="after")
    def _exact_content_address(self) -> Artifact:
        ref = Ref(self.ref)
        if (
            ref.revision is None
            or str(ref.without_revision()) != f"coreref-artifact.file/{self.sha256}"
        ):
            raise ValueError("Artifact links require an exact content-addressed reference")
        return self

    def verify(self, path: Path) -> None:
        if (
            not path.is_file()
            or path.stat().st_size != self.size
            or file_digest(path) != self.sha256
        ):
            raise ValueError(f"Artifact verification failed for {self.ref}")


class OutputIndex(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    key: Digest
    outputs: dict[str, Artifact]

    @field_validator("outputs")
    @classmethod
    def _output_names(cls, outputs: dict[str, Artifact]) -> dict[str, Artifact]:
        for name in outputs:
            path = Path(name)
            if (
                path.is_absolute()
                or not path.parts
                or ".." in path.parts
                or name != path.as_posix()
            ):
                raise ValueError(f"Invalid output name {name!r}")
        return outputs

    def extend(self, previous: OutputIndex) -> OutputIndex:
        """Merge earlier outputs of the same slice, rejecting conflicting files."""
        if self.key != previous.key:
            raise ValueError("Cannot combine outputs of different slices")
        changed = {
            name
            for name in self.outputs.keys() & previous.outputs.keys()
            if self.outputs[name] != previous.outputs[name]
        }
        if changed:
            raise ValueError(f"Previously published outputs changed: {', '.join(sorted(changed))}")
        return OutputIndex(key=self.key, outputs=previous.outputs | self.outputs)

    def document(self) -> JSON:
        return {
            "schema_version": self.schema_version,
            "key": self.key,
            "outputs": {
                name: {"ref": item.ref, "size": item.size, "sha256": item.sha256}
                for name, item in self.outputs.items()
            },
        }

    @classmethod
    def from_record(cls, record: Record) -> OutputIndex | None:
        payload = record.payload
        if not isinstance(payload, Mapping) or "output_index" not in payload:
            return None
        # CoreRef exposes nested metadata as immutable mappings.
        return cls.model_validate_json(json.dumps(payload["output_index"]))


class ArtifactStore:
    """Pack individual files, verify exact links, and upload indexed contents."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self._packed: dict[str, Artifact] = {}

    def pack(self, path: Path) -> Artifact:
        sha256 = file_digest(path)
        cached = self._packed.get(sha256)
        if cached is not None:
            return cached
        record = LocalRecord(Ref(f"coreref-artifact.file/{sha256}"), files={"data": path})
        installed = self.catalog._pack(record)
        artifact = Artifact(ref=str(installed.ref), sha256=sha256, size=path.stat().st_size)
        artifact.verify(installed.files["data"])
        self._packed[sha256] = artifact
        return artifact

    def pack_index(self, key: str, files: Mapping[str, Path]) -> OutputIndex:
        """Create an index pointing to content already packed in the local cache."""
        return OutputIndex(key=key, outputs={name: self.pack(path) for name, path in files.items()})

    def get(self, artifact: Artifact) -> Path:
        try:
            installed = self.catalog.get(Ref(artifact.ref))
        except NotFound as exc:
            raise NotFound(f"Missing indexed artifact {artifact.ref}") from exc
        path = installed.files.get("data")
        if path is None:
            raise ValueError(f"Missing data file in artifact {artifact.ref}")
        artifact.verify(path)
        return path

    def upload(self, outputs: Mapping[str, Artifact]) -> None:
        for ref in sorted({Ref(item.ref) for item in outputs.values()}):
            self.catalog._upload(ref)
