"""Public domain models import without Conan; the adapter is loaded explicitly."""

from __future__ import annotations

import subprocess
import sys
from textwrap import dedent

from coreref.api.packages.catalog import Catalog


def test_public_models_import_independently_of_conan(catalog: Catalog) -> None:
    code = dedent("""
        import sys
        from pathlib import Path

        from coreref import api

        assert "pydantic" not in sys.modules
        assert "networkx" not in sys.modules
        assert "conan" not in sys.modules

        from coreref.api import errors

        assert "coreref.api.packages.records" not in sys.modules
        assert "pydantic" not in sys.modules
        assert "conan" not in sys.modules

        from coreref.api.packages import records

        assert "networkx" not in sys.modules
        assert "pydantic" not in sys.modules

        offline = records.Ref("public.offline/v1")
        assert str(offline) == "public.offline/v1"
        assert "conan" not in sys.modules

        from coreref.api.projects.graph import Graph
        assert "conan" not in sys.modules

        from coreref.api.packages import catalog
        from coreref.api.packages import query

        manifest: dict[str, records.JSON] = {"views": ["rtl", "timing"]}
        instance = catalog.Catalog(Path(sys.argv[1]))
        local = records.LocalRecord(
            ref=records.Ref("public.rtl/v1"),
            payload=manifest,
        )
        record = instance.publish(record=local)
        stored = instance.get(record.ref)
        assert stored.ref == record.ref
        assert stored.payload["views"] == ("rtl", "timing")
        assert instance.search_refs(query.SearchQuery()) == (stored.ref,)
        assert Graph(instance, record.ref).dependencies() == ()
        resolved = Graph(instance, local)
        assert isinstance(resolved.records[0], records.LocalRecord)
        assert resolved.record is local
        assert local.payload is manifest
        assert local.payload == {"views": ["rtl", "timing"]}
        try:
            instance.get(records.Ref("public.absent/v1"))
        except errors.NotFound:
            pass
        else:
            raise AssertionError("Domain errors must use the public error contract")

        assert "dulwich" not in sys.modules, "Conan source retrieval uses its native Git helper"
    """)
    result = subprocess.run(  # noqa: S603 - fixed script with the current Python interpreter
        [sys.executable, "-I", "-c", code, str(catalog._client.home)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
