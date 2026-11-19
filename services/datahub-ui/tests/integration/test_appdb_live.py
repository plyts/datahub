"""Integration tests of the application database against a real PostgreSQL.

DATAHUB_TEST_PG_URL=postgresql://datahub_app:...@localhost:5432/datahub
DATAHUB_TEST_PG_RO_URL=postgresql://datahub_ro:...@localhost:5432/datahub   (optional)
"""

import os
import uuid

import psycopg
import pytest

from datahub_ui.db.migrate import migrate, migration_files
from datahub_ui.db.repository import AppDB, ValidationError

pytestmark = pytest.mark.integration
URL = os.getenv("DATAHUB_TEST_PG_URL")
RO_URL = os.getenv("DATAHUB_TEST_PG_RO_URL")

if not URL:
    pytest.skip("DATAHUB_TEST_PG_URL not set", allow_module_level=True)


@pytest.fixture(scope="module")
def db() -> AppDB:
    migrate(URL)
    return AppDB(URL)


def test_migrations_are_idempotent(db):
    assert migrate(URL) == []
    with psycopg.connect(URL) as conn:
        applied = {r[0] for r in conn.execute("select version from datahub.schema_migrations")}
    assert applied == {p.name for p in migration_files()}


def test_glossary_is_seeded(db):
    assert {"Commande", "Chiffre d'affaires", "Client"} <= {t["term"] for t in db.list_terms()}


def test_pipeline_crud_and_validation(db):
    name = f"it_{uuid.uuid4().hex[:8]}"
    saved = db.save_pipeline(
        {
            "name": name,
            "selection_type": "dagster",
            "selection": "group:staging",
            "cron": "0 6 * * *",
            "tags": {"team": "data"},
        },
        "alice",
    )
    assert saved["created_by"] == "alice" and saved["tags"] == {"team": "data"}
    updated = db.save_pipeline(
        {"name": name, "selection_type": "dbt", "selection": "tag:finance", "enabled": False}, "bob"
    )
    assert updated["selection_type"] == "dbt" and updated["updated_by"] == "bob" and updated["created_by"] == "alice"
    assert updated["cron"] is None
    with pytest.raises(ValidationError):
        db.save_pipeline({"name": "Bad Name", "selection_type": "dagster", "selection": "x"}, "alice")
    with pytest.raises(psycopg.errors.CheckViolation):
        with db._conn() as conn:
            conn.execute(
                "insert into datahub.pipeline_definitions (name, selection_type, selection, created_by, updated_by)"
                " values ('ok_name_x', 'sql', 'x', 'a', 'a')"
            )
    assert db.delete_pipeline(name) and not db.delete_pipeline(name)


def test_connections_never_store_secret_values(db):
    with pytest.raises(ValidationError):
        db.save_connection({"name": "crm", "kind": "postgresql", "secret_ref": "p@ssw0rd!"}, "admin")
    row = db.save_connection(
        {
            "name": f"crm_{uuid.uuid4().hex[:6]}",
            "kind": "postgresql",
            "host": "crm.local",
            "port": 5432,
            "secret_ref": "CRM_DB_PASSWORD",
        },
        "admin",
    )
    assert row["secret_ref"] == "CRM_DB_PASSWORD"
    db.delete_connection(row["name"])


def test_incidents_are_idempotent_per_run(db):
    run_id = uuid.uuid4().hex
    assert db.record_incident(run_id, "daily_partition_job", "Erreur SQL / dbt", "high", "boom") is True
    assert db.record_incident(run_id, "daily_partition_job", "Erreur SQL / dbt", "high", "boom") is False
    incident = next(i for i in db.list_incidents() if i["run_id"] == run_id)
    resolved = db.update_incident(incident["id"], "resolved", "alice", "fixed upstream")
    assert resolved["status"] == "resolved" and resolved["resolved_at"] is not None
    with pytest.raises(ValidationError):
        db.update_incident(incident["id"], "closed", None, "")


def test_audit_log(db):
    marker = uuid.uuid4().hex
    db.audit("alice", ["DATA_ENGINEER"], "pipeline.launch", marker, {"run_id": "x"})
    db.audit("alice", ["DATA_ENGINEER"], "pipeline.launch", marker, {"error": "y"}, success=False)
    rows = [r for r in db.list_audit(username="alice", action="pipeline.") if r["target"] == marker]
    assert len(rows) == 2 and rows[0]["success"] is False and rows[1]["details"] == {"run_id": "x"}


@pytest.mark.skipif(not RO_URL, reason="DATAHUB_TEST_PG_RO_URL not set")
def test_read_only_role_least_privilege(db):
    with psycopg.connect(RO_URL) as conn:
        conn.execute("select count(*) from datahub.pipeline_definitions").fetchone()
        conn.execute("select count(*) from datahub.audit_log").fetchone()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("insert into datahub.audit_log (username, action) values ('x', 'y')")
    with psycopg.connect(RO_URL) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select * from datahub.data_connections").fetchone()
