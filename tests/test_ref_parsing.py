"""Check the canonical component coordinates."""

from __future__ import annotations

import pytest

from coreref.api.errors import InvalidPackage
from coreref.api.packages.records import Ref


@pytest.mark.parametrize(
    "value",
    [
        "soc.cpu/1.0",
        "soc.cpu_core/2.0",
        "shared.stdlib/1.0",
        "pdk.techlib-n5/2.0",
        f"soc.{'component' * 30}/1.0+{'a' * 110}",
    ],
)
def test_parse_round_trip(value: str) -> None:
    ref = Ref(value)
    assert str(ref) == value
    assert Ref(str(ref)) == ref
    assert {ref: value}[Ref(value)] == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "soc.cpu.rtl/1.0",
        "soc.cpu.rtl.default/1.0",
        "soc.cpu.alu.be.lib-lef/1.0",
        "shared.stdlib/[>=1.0 <2.0]",
        "soc.cpu/1.0#",
        "soc.cpu/1.0#abc123",
        "soc.cpu..be.lib-lef/1.0",
        "soc.cpu.alu.be.lib-lef.extra/1.0",
        "soc.cpu.be.lib-lef..corner.tt/1.0",
        "soc.cpu/1.0@user/channel",
    ],
)
def test_parse_rejects_noncanonical_coordinates(value: str) -> None:
    with pytest.raises(InvalidPackage, match="Expected namespace"):
        Ref(value)


def test_reference_exposes_its_coordinates() -> None:
    ref = Ref("soc.cpu_core/2.1")
    assert (ref.namespace, ref.component, ref.version) == ("soc", "cpu_core", "2.1")
    assert ref.package_name == "soc.cpu_core"
