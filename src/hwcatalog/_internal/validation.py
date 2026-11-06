"""Pure input checks and detached JSON/file snapshots."""

import json
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from frozendict import deepfreeze, frozendict

from ..api.errors import InvalidPackage
from ..api.protocols import JSONSerializable
from ..api.types import JSON


def checked_text(value: str, *, blank: bool = False) -> str:
    if not isinstance(value, str) or (not blank and not value.strip()):
        raise InvalidPackage("Expected a nonempty string" if not blank else "Expected a string")
    return str(value)


def snapshot_payload(value: JSON | JSONSerializable) -> JSON:
    """Serialize a consumer object once and detach every nested JSON container."""
    if isinstance(value, JSONSerializable):
        value = json.loads(value.to_json())
    snapshot: JSON = deepfreeze(json.loads(json.dumps(value, allow_nan=False)))
    return snapshot


def snapshot_files(files: Mapping[str, Path]) -> Mapping[str, Path]:
    """Validate package file coordinates without accessing the filesystem."""
    for name, path in files.items():
        validate_file_name(name)
        if not isinstance(path, Path) or not path.is_absolute():
            raise InvalidPackage("Package file paths must be absolute Path values")
    return frozendict(files)


def validate_file_name(name: str) -> None:
    path = PurePosixPath(checked_text(name))
    if path.is_absolute() or any(char in name for char in ("\\", ":", "\0")):
        raise InvalidPackage("Artifact names must be relative POSIX paths")
    if ".." in path.parts or str(path) != name or not path.parts:
        raise InvalidPackage("Artifact names must be canonical without parent traversal")
