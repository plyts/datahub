"""Minimal, idempotent SQL migration runner (`python -m datahub_ui.db.migrate`).

Each file of `migrations/` is applied once, in lexical order, inside a transaction,
and recorded in `datahub.schema_migrations`. A PostgreSQL advisory lock prevents two
containers from migrating concurrently.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
LOCK_ID = 7_340_001

log = logging.getLogger("datahub.migrate")


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def migrate(db_url: str) -> list[str]:
    applied_now: list[str] = []
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("select pg_advisory_lock(%s)", [LOCK_ID])
        try:
            conn.execute("create schema if not exists datahub")
            conn.execute(
                "create table if not exists datahub.schema_migrations ("
                " version text primary key, applied_at timestamptz not null default now())"
            )
            done = {row[0] for row in conn.execute("select version from datahub.schema_migrations")}
            for path in migration_files():
                if path.name in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute("insert into datahub.schema_migrations (version) values (%s)", [path.name])
                applied_now.append(path.name)
                log.info("Applied migration %s", path.name)
        finally:
            conn.execute("select pg_advisory_unlock(%s)", [LOCK_ID])
    return applied_now


def main() -> int:
    from datahub_ui.config import get_settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    url = get_settings().app_db_url
    if not url:
        log.error("DATAHUB_APP_DB_URL is not set")
        return 1
    applied = migrate(url)
    log.info("Migrations applied: %s", applied or "none (up to date)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
