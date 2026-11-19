"""Data access layer for the Data Hub application database (PostgreSQL, schema `datahub`)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

PIPELINE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
SECRET_REF_RE = re.compile(r"^[A-Z0-9_]*$")
CONNECTION_KINDS = ("postgresql", "duckdb", "mysql", "sqlserver", "oracle", "s3", "sftp", "http", "other")
INCIDENT_STATUSES = ("open", "acknowledged", "resolved")


class ValidationError(ValueError):
    pass


@dataclass
class AppDB:
    url: str
    connect_timeout: int = 5

    def _conn(self):
        return psycopg.connect(self.url, row_factory=dict_row, connect_timeout=self.connect_timeout, autocommit=True)

    def ping(self) -> bool:
        try:
            with self._conn() as conn:
                conn.execute("select 1")
            return True
        except psycopg.Error:
            return False

    # ------------------------------------------------------------------ pipelines
    def list_pipelines(self) -> list[dict]:
        with self._conn() as conn:
            return conn.execute("select * from datahub.pipeline_definitions order by name").fetchall()

    def save_pipeline(self, data: dict[str, Any], username: str) -> dict:
        name = (data.get("name") or "").strip()
        if not PIPELINE_NAME_RE.match(name):
            raise ValidationError("Nom invalide : minuscules, chiffres et _ (3 à 63 caractères).")
        if data.get("selection_type") not in ("dagster", "dbt"):
            raise ValidationError("Type de sélection invalide.")
        if not (data.get("selection") or "").strip():
            raise ValidationError("La sélection est obligatoire.")
        with self._conn() as conn:
            return conn.execute(
                """
                insert into datahub.pipeline_definitions
                    (name, description, selection_type, selection, cron, timezone, enabled, tags, created_by, updated_by)
                values (%(name)s, %(description)s, %(selection_type)s, %(selection)s, %(cron)s, %(timezone)s,
                        %(enabled)s, %(tags)s, %(user)s, %(user)s)
                on conflict (name) do update set
                    description = excluded.description, selection_type = excluded.selection_type,
                    selection = excluded.selection, cron = excluded.cron, timezone = excluded.timezone,
                    enabled = excluded.enabled, tags = excluded.tags,
                    updated_by = excluded.updated_by, updated_at = now()
                returning *
                """,
                {
                    "name": name,
                    "description": data.get("description", ""),
                    "selection_type": data["selection_type"],
                    "selection": data["selection"].strip(),
                    "cron": (data.get("cron") or "").strip() or None,
                    "timezone": data.get("timezone") or "Europe/Paris",
                    "enabled": bool(data.get("enabled", True)),
                    "tags": Jsonb(data.get("tags") or {}),
                    "user": username,
                },
            ).fetchone()

    def delete_pipeline(self, name: str) -> bool:
        with self._conn() as conn:
            return conn.execute("delete from datahub.pipeline_definitions where name = %s", [name]).rowcount == 1

    # ------------------------------------------------------------------- glossary
    def list_terms(self) -> list[dict]:
        with self._conn() as conn:
            return conn.execute("select * from datahub.glossary_terms order by term").fetchall()

    def save_term(self, data: dict[str, Any], username: str) -> dict:
        term = (data.get("term") or "").strip()
        definition = (data.get("definition") or "").strip()
        if not term or not definition:
            raise ValidationError("Le terme et sa définition sont obligatoires.")
        with self._conn() as conn:
            return conn.execute(
                """
                insert into datahub.glossary_terms (term, definition, owner, synonyms, related_assets, created_by, updated_by)
                values (%(term)s, %(definition)s, %(owner)s, %(synonyms)s, %(assets)s, %(user)s, %(user)s)
                on conflict (term) do update set
                    definition = excluded.definition, owner = excluded.owner, synonyms = excluded.synonyms,
                    related_assets = excluded.related_assets, updated_by = excluded.updated_by, updated_at = now()
                returning *
                """,
                {
                    "term": term,
                    "definition": definition,
                    "owner": data.get("owner", ""),
                    "synonyms": [s.strip() for s in data.get("synonyms", []) if s.strip()],
                    "assets": [a.strip() for a in data.get("related_assets", []) if a.strip()],
                    "user": username,
                },
            ).fetchone()

    def delete_term(self, term: str) -> bool:
        with self._conn() as conn:
            return conn.execute("delete from datahub.glossary_terms where term = %s", [term]).rowcount == 1

    # ---------------------------------------------------------------- connections
    def list_connections(self) -> list[dict]:
        with self._conn() as conn:
            return conn.execute("select * from datahub.data_connections order by name").fetchall()

    def save_connection(self, data: dict[str, Any], username: str) -> dict:
        name = (data.get("name") or "").strip()
        if not name:
            raise ValidationError("Le nom est obligatoire.")
        if data.get("kind") not in CONNECTION_KINDS:
            raise ValidationError("Type de connexion invalide.")
        secret_ref = (data.get("secret_ref") or "").strip()
        if not SECRET_REF_RE.match(secret_ref):
            raise ValidationError(
                "La référence de secret doit être un NOM de variable (A-Z, 0-9, _), jamais une valeur."
            )
        with self._conn() as conn:
            return conn.execute(
                """
                insert into datahub.data_connections
                    (name, kind, host, port, database, username, secret_ref, options, description, created_by, updated_by)
                values (%(name)s, %(kind)s, %(host)s, %(port)s, %(database)s, %(username)s, %(secret_ref)s,
                        %(options)s, %(description)s, %(user)s, %(user)s)
                on conflict (name) do update set
                    kind = excluded.kind, host = excluded.host, port = excluded.port, database = excluded.database,
                    username = excluded.username, secret_ref = excluded.secret_ref, options = excluded.options,
                    description = excluded.description, updated_by = excluded.updated_by, updated_at = now()
                returning *
                """,
                {
                    "name": name,
                    "kind": data["kind"],
                    "host": data.get("host", ""),
                    "port": data.get("port") or None,
                    "database": data.get("database", ""),
                    "username": data.get("username", ""),
                    "secret_ref": secret_ref,
                    "options": Jsonb(data.get("options") or {}),
                    "description": data.get("description", ""),
                    "user": username,
                },
            ).fetchone()

    def delete_connection(self, name: str) -> bool:
        with self._conn() as conn:
            return conn.execute("delete from datahub.data_connections where name = %s", [name]).rowcount == 1

    # ------------------------------------------------------------------ incidents
    def list_incidents(self, status: str | None = None, limit: int = 200) -> list[dict]:
        with self._conn() as conn:
            if status:
                return conn.execute(
                    "select * from datahub.incidents where status = %s order by created_at desc limit %s",
                    [status, limit],
                ).fetchall()
            return conn.execute("select * from datahub.incidents order by created_at desc limit %s", [limit]).fetchall()

    def record_incident(self, run_id: str, job_name: str, category: str, severity: str, message: str) -> bool:
        """Create the incident of a failed run (idempotent). Returns True if created."""
        with self._conn() as conn:
            return (
                conn.execute(
                    """insert into datahub.incidents (run_id, job_name, category, severity, message)
                       values (%s, %s, %s, %s, %s) on conflict (run_id) do nothing""",
                    [run_id, job_name, category, severity, message[:4000]],
                ).rowcount
                == 1
            )

    def update_incident(self, incident_id: int, status: str, assignee: str | None, comment: str) -> dict:
        if status not in INCIDENT_STATUSES:
            raise ValidationError("Statut invalide.")
        with self._conn() as conn:
            return conn.execute(
                """update datahub.incidents set status = %s, assignee = %s, comment = %s, updated_at = now(),
                          resolved_at = case when %s = 'resolved' then now() else null end
                   where id = %s returning *""",
                [status, assignee, comment, status, incident_id],
            ).fetchone()

    # ---------------------------------------------------------------------- audit
    def audit(
        self,
        username: str,
        roles: list[str],
        action: str,
        target: str = "",
        details: dict | None = None,
        success: bool = True,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """insert into datahub.audit_log (username, roles, action, target, details, success)
                   values (%s, %s, %s, %s, %s, %s)""",
                [username, list(roles), action, target, Jsonb(details or {}), success],
            )

    def list_audit(self, limit: int = 500, username: str | None = None, action: str | None = None) -> list[dict]:
        clauses, params = [], []
        if username:
            clauses.append("username = %s")
            params.append(username)
        if action:
            clauses.append("action like %s")
            params.append(f"{action}%")
        where = f"where {' and '.join(clauses)}" if clauses else ""
        with self._conn() as conn:
            return conn.execute(
                f"select * from datahub.audit_log {where} order by ts desc limit %s", [*params, limit]
            ).fetchall()
