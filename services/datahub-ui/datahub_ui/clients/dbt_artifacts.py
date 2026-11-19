"""Read dbt artifacts (manifest.json, catalog.json, run_results.json) published by the `dbt_docs_job`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DOC_RESOURCES = ("model", "seed", "source", "snapshot")


@dataclass
class DbtArtifacts:
    manifest: dict[str, Any]
    catalog: dict[str, Any]
    run_results: dict[str, Any]

    @classmethod
    def load(cls, directory: str | Path) -> DbtArtifacts:
        root = Path(directory)

        def read(name: str) -> dict:
            path = root / name
            if not path.exists():
                return {}
            return json.loads(path.read_text(encoding="utf-8"))

        return cls(manifest=read("manifest.json"), catalog=read("catalog.json"), run_results=read("run_results.json"))

    @property
    def available(self) -> bool:
        return bool(self.manifest)

    @property
    def generated_at(self) -> str | None:
        return (self.catalog.get("metadata") or self.manifest.get("metadata") or {}).get("generated_at")

    def all_nodes(self) -> dict[str, dict]:
        return {**self.manifest.get("nodes", {}), **self.manifest.get("sources", {})}

    def documented_nodes(self) -> list[dict]:
        """Models, seeds, snapshots and sources with their catalog information merged."""
        out = []
        for unique_id, node in self.all_nodes().items():
            if node.get("resource_type") not in DOC_RESOURCES:
                continue
            cat = self.catalog.get("nodes", {}).get(unique_id) or self.catalog.get("sources", {}).get(unique_id) or {}
            stats = cat.get("stats", {})
            out.append(
                {
                    "unique_id": unique_id,
                    "name": node["name"],
                    "resource_type": node["resource_type"],
                    "schema": node.get("schema"),
                    "relation": f"{node.get('schema')}.{node.get('identifier') or node.get('alias') or node['name']}",
                    "asset_key": f"{node.get('schema')}/{node['name']}"
                    if node["resource_type"] != "source"
                    else f"{node.get('source_name')}/{node['name']}",
                    "description": node.get("description", ""),
                    "materialized": (node.get("config") or {}).get("materialized", node["resource_type"]),
                    "tags": node.get("tags", []),
                    "owner": (node.get("meta") or {}).get("owner")
                    or (node.get("config", {}).get("meta") or {}).get("owner", ""),
                    "glossary": (node.get("meta") or {}).get("glossary")
                    or (node.get("config", {}).get("meta") or {}).get("glossary", []),
                    "path": node.get("original_file_path", ""),
                    "row_count": (stats.get("row_count") or {}).get("value"),
                    "columns": self.columns(unique_id),
                }
            )
        return sorted(out, key=lambda n: (n["schema"] or "", n["name"]))

    def columns(self, unique_id: str) -> list[dict]:
        node = self.all_nodes().get(unique_id, {})
        cat = self.catalog.get("nodes", {}).get(unique_id) or self.catalog.get("sources", {}).get(unique_id) or {}
        documented = node.get("columns", {})
        catalog_cols = cat.get("columns", {})
        names = list(catalog_cols) or list(documented)
        tests = self.tests_by_column(unique_id)
        cols = []
        for name in names:
            doc = documented.get(name) or documented.get(name.lower()) or {}
            cols.append(
                {
                    "name": name,
                    "type": (catalog_cols.get(name) or {}).get("type") or doc.get("data_type") or "",
                    "description": doc.get("description", ""),
                    "pii": bool((doc.get("meta") or {}).get("pii")),
                    "tests": tests.get(name.lower(), []),
                }
            )
        return cols

    def tests_by_column(self, unique_id: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for node in self.manifest.get("nodes", {}).values():
            if node.get("resource_type") != "test" or node.get("attached_node") != unique_id:
                continue
            column = (node.get("column_name") or "").lower()
            test_name = (node.get("test_metadata") or {}).get("name") or node["name"]
            out.setdefault(column, []).append(test_name)
        return out

    def sql(self, unique_id: str) -> tuple[str, str]:
        """(raw SQL, compiled SQL) of a model."""
        node = self.all_nodes().get(unique_id, {})
        return node.get("raw_code", ""), node.get("compiled_code", "")

    def test_results(self) -> list[dict]:
        rows = []
        nodes = self.manifest.get("nodes", {})
        for r in self.run_results.get("results", []):
            node = nodes.get(r["unique_id"], {})
            if node.get("resource_type") not in ("test", "unit_test"):
                continue
            rows.append(
                {
                    "test": node.get("name", r["unique_id"]),
                    "status": r["status"],
                    "failures": r.get("failures"),
                    "time_s": r.get("execution_time"),
                    "model": node.get("attached_node", ""),
                }
            )
        return rows

    def exposures(self) -> list[dict]:
        return list(self.manifest.get("exposures", {}).values())
