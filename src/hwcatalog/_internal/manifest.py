"""Validation of a consumer's single-package JSON document."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from ..api.errors import InvalidPackage
from ..api.models.records import Record, Ref
from ..api.models.resources import GitResource
from ..api.types import JSON
from .validation import normalize_record, validate_file_name


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Ref(_Model):
    name: str
    version: str
    kind: str
    namespace: str

    def ref(self) -> Ref:
        return Ref(**self.model_dump())


class _Checkout(_Model):
    path: str


class _Git(_Model):
    repository_url: str
    resolved_commit: str
    requested_ref: str = "HEAD"
    provider: Literal["git"] = "git"


class _Document(_Model):
    ref: _Ref
    payload: JsonValue = None
    dependencies: list[_Ref] = Field(default_factory=list)
    source: _Checkout | _Git | None = None
    files: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True)
class Manifest:
    record: Record
    dependencies: tuple[Ref, ...]
    source_path: Path | None
    files: dict[str, Path]


def read_manifest(path: Path) -> Manifest:
    """Resolve input paths relative to the JSON file, never the working directory."""
    try:
        path = path.expanduser().resolve(strict=True)
        document = _Document.model_validate_json(path.read_text())
        source = document.source
        git = GitResource(**source.model_dump()) if isinstance(source, _Git) else None
        checkout = _local_path(path, source.path) if isinstance(source, _Checkout) else None
        if checkout is not None and not checkout.is_dir():
            raise InvalidPackage("Git source path must be a repository directory")
        files: dict[str, Path] = {}
        for name, locator in document.files.items():
            validate_file_name(name)
            local = _local_path(path, locator)
            if not local.is_file():
                raise InvalidPackage(f"Artifact is not a regular file: {locator}")
            if any(name.startswith(other + "/") or other.startswith(name + "/") for other in files):
                raise InvalidPackage("Artifact paths cannot overlap")
            files[name] = local
        record = normalize_record(
            Record(document.ref.ref(), cast(JSON, document.payload), git, tuple(sorted(files)))
        )
        dependencies = tuple(item.ref() for item in document.dependencies)
        return Manifest(record, dependencies, checkout, files)
    except (OSError, ValueError, TypeError, ValidationError) as exc:
        raise InvalidPackage(f"Invalid package description {path}: {exc}") from exc


def _local_path(manifest: Path, locator: str) -> Path:
    return (manifest.parent / Path(locator).expanduser()).resolve(strict=True)
