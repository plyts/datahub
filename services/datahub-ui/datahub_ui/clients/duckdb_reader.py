"""Read-only access to the DuckDB analytical database (data preview in the catalog).

DuckDB allows one writer process at a time: while a pipeline is running, the file is
locked and the preview is temporarily unavailable (a clear message is shown).
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DatabaseBusy(RuntimeError):
    pass


def _check_ident(*names: str) -> None:
    for name in names:
        if not IDENT.match(name or ""):
            raise ValueError(f"Identifiant invalide : {name!r}")


def preview(path: str, schema: str, table: str, limit: int = 100) -> pd.DataFrame:
    _check_ident(schema, table)
    if not Path(path).exists():
        raise FileNotFoundError(path)
    try:
        with duckdb.connect(path, read_only=True) as conn:
            return conn.execute(f'select * from "{schema}"."{table}" limit ?', [int(limit)]).fetchdf()
    except duckdb.IOException as exc:
        raise DatabaseBusy(
            "La base DuckDB est en cours d'écriture par un pipeline ; réessayez dans un instant."
        ) from exc


def table_sizes(path: str) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame(columns=["schema", "table", "estimated_rows"])
    try:
        with duckdb.connect(path, read_only=True) as conn:
            return conn.execute(
                "select schema_name as schema, table_name as table, estimated_size as estimated_rows "
                "from duckdb_tables() order by 1, 2"
            ).fetchdf()
    except duckdb.IOException as exc:
        raise DatabaseBusy("Base DuckDB occupée.") from exc
