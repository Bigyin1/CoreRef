"""Public domain models import without Conan; the adapter is loaded explicitly."""

from __future__ import annotations

import subprocess
import sys
from textwrap import dedent

from coreref.api.catalog import Catalog


def test_public_models_import_independently_of_conan(catalog: Catalog) -> None:
    code = dedent("""
        import sys
        from pathlib import Path

        from coreref import api

        assert "pydantic" not in sys.modules
        assert "networkx" not in sys.modules
        assert "conan" not in sys.modules

        from coreref.api import errors

        assert "coreref.api.records" not in sys.modules
        assert "pydantic" not in sys.modules
        assert "conan" not in sys.modules

        from coreref.api import records

        assert "networkx" not in sys.modules
        assert "pydantic" not in sys.modules

        offline = records.Ref("public.offline.rtl.default/v1")
        assert str(offline) == "public.offline.rtl.default/v1"
        assert "conan" not in sys.modules

        from coreref.api.graph import Graph
        assert "conan" not in sys.modules

        from coreref.api import catalog
        from coreref.api import query

        manifest: dict[str, records.JSON] = {"views": ["rtl", "timing"]}
        instance = catalog.Catalog(Path(sys.argv[1]))
        local = records.LocalRecord(
            ref=records.Ref("public.rtl.rtl.default/v1"),
            payload=manifest,
        )
        record = instance.publish(record=local)
        stored = instance.get(record.ref)
        assert stored.ref == record.ref
        assert stored.payload["views"] == ("rtl", "timing")
        assert instance.search(query.SearchQuery()) == (stored,)
        assert Graph(instance, record.ref).dependencies() == ()
        resolved = Graph(instance, local)
        assert isinstance(resolved.records[0], records.LocalRecord)
        assert resolved.records[0].payload == stored.payload
        try:
            instance.get(records.Ref("public.absent.rtl.default/v1"))
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
