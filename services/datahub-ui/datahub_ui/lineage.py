"""Lineage: asset graph traversal, impact analysis, Graphviz rendering and column-level lineage."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

import sqlglot
from sqlglot import exp
from sqlglot.lineage import lineage as sqlglot_lineage

LAYER_COLORS = {
    "raw": "#F4E1C1",
    "reference": "#E3E3E3",
    "staging": "#D6EAF8",
    "intermediate": "#D1F2EB",
    "marts": "#FAD7A0",
}


def build_graph(assets: Iterable[dict]) -> dict[str, set[str]]:
    """Adjacency (upstream -> downstream) from Dagster asset nodes {key, deps}."""
    graph: dict[str, set[str]] = {}
    for asset in assets:
        graph.setdefault(asset["key"], set())
        for dep in asset.get("deps", []):
            graph.setdefault(dep, set()).add(asset["key"])
    return graph


def reverse(graph: dict[str, set[str]]) -> dict[str, set[str]]:
    rev: dict[str, set[str]] = {node: set() for node in graph}
    for node, children in graph.items():
        for child in children:
            rev.setdefault(child, set()).add(node)
    return rev


def traverse(graph: dict[str, set[str]], start: str, max_depth: int | None = None) -> dict[str, int]:
    """Breadth-first traversal: {node: distance} excluding the start node."""
    seen: dict[str, int] = {}
    queue = deque([(start, 0)])
    while queue:
        node, depth = queue.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        for nxt in graph.get(node, ()):
            if nxt not in seen and nxt != start:
                seen[nxt] = depth + 1
                queue.append((nxt, depth + 1))
    return seen


def upstream(graph, node, max_depth=None) -> dict[str, int]:
    return traverse(reverse(graph), node, max_depth)


def downstream(graph, node, max_depth=None) -> dict[str, int]:
    return traverse(graph, node, max_depth)


def impact_analysis(graph: dict[str, set[str]], node: str, exposures: list[dict] | None = None) -> dict:
    """What breaks if `node` changes or fails: downstream assets by layer + dbt exposures."""
    impacted = downstream(graph, node)
    by_layer: dict[str, list[str]] = {}
    for key in sorted(impacted, key=lambda k: (impacted[k], k)):
        by_layer.setdefault(key.split("/")[0], []).append(key)
    impacted_names = {k.split("/")[-1] for k in impacted} | {node.split("/")[-1]}
    hit_exposures = [
        e.get("label") or e.get("name")
        for e in exposures or []
        if any(dep.split(".")[-1] in impacted_names for dep in (e.get("depends_on") or {}).get("nodes", []))
    ]
    return {
        "count": len(impacted),
        "by_layer": by_layer,
        "exposures": hit_exposures,
        "max_depth": max(impacted.values(), default=0),
    }


def to_dot(
    graph: dict[str, set[str]],
    focus: str | None = None,
    nodes: set[str] | None = None,
    statuses: dict[str, str] | None = None,
) -> str:
    """Graphviz DOT source (rendered client-side by st.graphviz_chart)."""
    keep = nodes if nodes is not None else set(graph)
    lines = [
        "digraph lineage {",
        '  rankdir=LR; node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10];',
    ]
    for node in sorted(keep):
        layer = node.split("/")[0]
        color = LAYER_COLORS.get(layer, "#FFFFFF")
        border = {"FAILURE": "#C0392B", "WARN": "#E67E22"}.get((statuses or {}).get(node, ""), "#555555")
        width = "2.5" if node == focus else "1"
        lines.append(f'  "{node}" [fillcolor="{color}", color="{border}", penwidth={width}];')
    for node in sorted(keep):
        for child in sorted(graph.get(node, ())):
            if child in keep:
                lines.append(f'  "{node}" -> "{child}";')
    lines.append("}")
    return "\n".join(lines)


def column_lineage(compiled_sql: str, column: str, schema: dict | None = None, dialect: str = "duckdb") -> list[dict]:
    """Source columns feeding `column` of a compiled dbt model (best effort, via sqlglot).

    Returns [{"table": "staging.stg_orders", "column": "amount"}, ...]. Empty if it cannot be resolved.
    """
    if not compiled_sql.strip():
        return []
    try:
        root = sqlglot_lineage(column, compiled_sql, schema=schema or None, dialect=dialect)
    except (sqlglot.errors.SqlglotError, ValueError, KeyError, AttributeError):
        return []
    found: dict[tuple[str, str], None] = {}
    for node in root.walk():
        if node.downstream:
            continue
        if isinstance(node.expression, exp.Table):
            table = node.expression
            name = ".".join(p for p in (table.db, table.name) if p)
            found[(name, node.name.split(".")[-1])] = None
    return [{"table": t, "column": c} for t, c in found]


def catalog_schema(catalog: dict) -> dict:
    """sqlglot schema mapping {db: {table: {column: type}}} built from dbt catalog.json."""
    schema: dict = {}
    for section in ("nodes", "sources"):
        for node in (catalog.get(section) or {}).values():
            meta = node.get("metadata", {})
            db, table = meta.get("schema"), meta.get("name")
            if db and table:
                schema.setdefault(db, {})[table] = {c: v.get("type", "") for c, v in node.get("columns", {}).items()}
    return schema
