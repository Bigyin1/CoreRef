"""Explicit Conan setup for disposable examples and tests, without modifying user configuration."""

from __future__ import annotations

import platform
from pathlib import Path

from conan.api.conan_api import ConanAPI  # type: ignore[import-untyped]


def configured_home(home: Path) -> Path:
    """Prepare an owned cache with a normal default profile and no public remotes."""
    new_remotes = not (home / "remotes.json").exists()
    api = ConanAPI(cache_folder=str(home.resolve()))
    if new_remotes:
        api.remotes.disable("*")
    profiles = home / "profiles"
    profiles.mkdir(exist_ok=True)
    profile = profiles / "default"
    if not profile.exists():
        os_name = {"Darwin": "Macos"}.get(platform.system(), platform.system())
        profile.write_text(f"[settings]\nos={os_name}\narch=x86_64\nbuild_type=Release\n")
    return home
