"""Skip store integration on machines without Nix."""

import shutil

import pytest


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("nix") and shutil.which("nix") is None:
        pytest.skip("Nix is required for this test")
