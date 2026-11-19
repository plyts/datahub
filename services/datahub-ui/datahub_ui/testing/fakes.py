"""In-memory fake backends.

Used by the UI test-suite (`DATAHUB_UI_FAKE_BACKENDS=1`) and for an offline demo of the
interface. They implement the same methods as the real clients.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

NOW = time.time()
_ASSETS = [
    ("raw/customers", "ingestion", [], False),
    ("raw/orders", "ingestion", [], True),
    ("raw/payments", "ingestion", ["raw/orders"], True),
    ("reference/country_codes", "reference", [], False),
    ("staging/stg_customers", "staging", ["raw/customers"], False),
    ("staging/stg_orders", "staging", ["raw/orders"], False),
    ("staging/stg_payments", "staging", ["raw/payments"], False),
    ("intermediate/int_order_payments", "intermediate", ["staging/stg_payments"], False),
    ("marts/fct_orders", "marts_core", ["staging/stg_orders", "intermediate/int_order_payments"], True),
    (
        "marts/dim_customers",
        "marts_core",
        ["staging/stg_customers", "reference/country_codes", "marts/fct_orders"],
        False,
    ),
    ("marts/agg_daily_revenue", "marts_finance", ["marts/fct_orders"], False),
]


def _run(job: str, status: str, hours_ago: float, duration: float, partition: str | None = None) -> dict:
    start = NOW - hours_ago * 3600
    tags = {"dagster/partition": partition} if partition else {}
    return {
        "runId": str(uuid.uuid4()),
        "status": status,
        "jobName": job,
        "creationTime": start - 2,
        "startTime": start,
        "endTime": start + duration if status not in ("STARTED", "QUEUED") else None,
        "updateTime": start + duration,
        "parentRunId": None,
        "rootRunId": None,
        "canTerminate": status in ("STARTED", "QUEUED"),
        "tags": tags,
        "duration": duration if status not in ("STARTED", "QUEUED") else None,
    }


class FakeDagster:
    def __init__(self):
        self.launched: list[dict] = []
        self.schedule_status = {"daily_partition_schedule": "RUNNING", "weekly_marts_rebuild_schedule": "STOPPED"}
        self._runs = [
            _run(
                "daily_partition_job",
                "SUCCESS" if i != 3 else "FAILURE",
                24 * i + 1,
                40 + i * 2 + (60 if i == 1 else 0),
                (datetime.now(UTC) - timedelta(days=i + 1)).date().isoformat(),
            )
            for i in range(10)
        ] + [_run("dbt_docs_job", "SUCCESS", 2, 12)]

    def version(self):
        return "1.13.30"

    def ping(self):
        return True

    def locations(self):
        return [{"name": "datahub", "status": "LOADED", "updated": NOW, "error": None}]

    def daemon_health(self):
        return [
            {"daemonType": d, "healthy": True, "required": True, "lastHeartbeatTime": NOW}
            for d in ("SCHEDULER", "SENSOR", "QUEUED_RUN_COORDINATOR", "BACKFILL")
        ]

    def reload_location(self):
        return "LOADED"

    def jobs(self):
        return [
            {"name": n, "description": d, "isAssetJob": True, "tags": {"datahub/kind": k}}
            for n, d, k in [
                ("daily_partition_job", "Pipeline quotidien", "pipeline"),
                ("dbt_marts_job", "Reconstruction", "pipeline"),
                ("landing_files_job", "Fichiers CSV", "event"),
                ("dbt_docs_job", "Documentation dbt", "docs"),
            ]
        ]

    def runs(self, limit=50, job_name=None, statuses=None, created_after=None, tags=None):
        runs = [
            r
            for r in self.launched + self._runs
            if (not job_name or r["jobName"] == job_name)
            and (not statuses or r["status"] in statuses)
            and (not created_after or r["creationTime"] >= created_after)
        ]
        return runs[:limit]

    def run(self, run_id):
        run = next(r for r in self.launched + self._runs if r["runId"] == run_id)
        steps = [
            {"stepKey": s, "status": "SUCCESS", "startTime": run["startTime"], "endTime": run["startTime"] + 5 + i}
            for i, s in enumerate(["raw__orders", "raw__payments", "dbt_upstream", "dbt_partitioned", "dbt_downstream"])
        ]
        return {**run, "runConfigYaml": "{}", "stepStats": steps}

    def run_events(self, run_id, after_cursor=None, limit=2000):
        run = self.run(run_id)
        events = [
            {
                "__typename": "RunStartEvent",
                "message": "Started",
                "timestamp": str(int(run["startTime"] * 1000)),
                "level": "INFO",
                "stepKey": None,
                "eventType": "RUN_START",
            }
        ]
        if run["status"] == "FAILURE":
            events.append(
                {
                    "__typename": "ExecutionStepFailureEvent",
                    "message": "Step failed",
                    "timestamp": str(int(run["startTime"] * 1000) + 1000),
                    "level": "ERROR",
                    "stepKey": "dbt_upstream",
                    "eventType": "STEP_FAILURE",
                    "error": {
                        "message": 'Catalog Error: Table with name "orderz" does not exist',
                        "className": "DagsterDbtCliRuntimeError",
                        "stack": ["  File x.py\n"],
                    },
                }
            )
        return events, "cursor", False

    def run_errors(self, run_id):
        return [e for e in self.run_events(run_id)[0] if e.get("error") or e["level"] == "ERROR"]

    def launch_job(self, job_name, run_config=None, tags=None, partition_key=None):
        run = _run(job_name, "QUEUED", 0, 0, partition_key)
        self.launched.insert(0, run)
        return run["runId"]

    def reexecute(self, run_id, strategy="FROM_FAILURE", tags=None):
        return self.launch_job(self.run(run_id)["jobName"])

    def terminate(self, run_id):
        return None

    def schedules(self, future_ticks=5):
        return [
            {
                "name": n,
                "cron": c,
                "timezone": "Europe/Paris",
                "job": j,
                "description": "",
                "status": self.schedule_status[n],
                "state_id": n,
                "next_ticks": [NOW + 3600 * (i + 1) * 24 for i in range(future_ticks)],
            }
            for n, c, j in [
                ("daily_partition_schedule", "30 5 * * *", "daily_partition_job"),
                ("weekly_marts_rebuild_schedule", "0 3 * * 0", "dbt_marts_job"),
            ]
        ]

    def set_schedule(self, name, running):
        self.schedule_status[name] = "RUNNING" if running else "STOPPED"
        return self.schedule_status[name]

    def sensors(self):
        return [
            {
                "name": "landing_file_sensor",
                "description": "",
                "type": "STANDARD",
                "interval_s": 60,
                "targets": ["landing_files_job"],
                "status": "RUNNING",
                "state_id": "s1",
                "last_tick_status": "SKIPPED",
                "last_tick": NOW - 30,
            }
        ]

    def set_sensor(self, name, running):
        return "RUNNING" if running else "STOPPED"

    def assets(self):
        out = []
        for key, group, deps, partitioned in _ASSETS:
            dependents = [k for k, _, d, _ in _ASSETS if key in d]
            out.append(
                {
                    "key": key,
                    "path": key.split("/"),
                    "group": group,
                    "description": f"Asset {key}",
                    "kinds": ["dbt" if group not in ("ingestion",) else "python", "duckdb"],
                    "partitioned": partitioned,
                    "op": "op",
                    "has_checks": key.startswith(("raw", "marts")),
                    "owners": ["team:data-engineering"],
                    "deps": deps,
                    "dependents": dependents,
                    "partition_stats": {
                        "numMaterialized": 300,
                        "numPartitions": 322,
                        "numFailed": 2,
                        "numMaterializing": 0,
                    }
                    if partitioned
                    else None,
                    "freshness": "HEALTHY" if key != "marts/agg_daily_revenue" else "WARNING",
                    "last_materialization": NOW - 3600,
                    "last_run_id": self._runs[0]["runId"],
                }
            )
        return out

    def materializations(self, asset_key, limit=50):
        return [
            {
                "run_id": r["runId"],
                "timestamp": r["startTime"],
                "partition": r["tags"].get("dagster/partition"),
                "metadata": {"dagster/row_count": 80 + i},
            }
            for i, r in enumerate(self._runs[:limit])
        ]

    def asset_checks(self, asset_key):
        return [
            {
                "asset": asset_key,
                "name": f"unique_{asset_key.split('/')[-1]}_id",
                "description": "",
                "blocking": False,
                "status": "SUCCEEDED",
                "severity": "ERROR",
                "timestamp": NOW - 3600,
                "run_id": "r1",
            },
            {
                "asset": asset_key,
                "name": "orders_volume_anomaly",
                "description": "",
                "blocking": False,
                "status": "FAILED" if asset_key == "raw/orders" else "SUCCEEDED",
                "severity": "WARN",
                "timestamp": NOW - 3600,
                "run_id": "r1",
            },
        ]

    def check_history(self, asset_key, check_name, limit=30):
        return [
            {
                "run_id": f"r{i}",
                "status": "SUCCEEDED" if i % 4 else "FAILED",
                "timestamp": NOW - i * 86400,
                "partition": None,
                "severity": "WARN",
            }
            for i in range(10)
        ]

    def partition_keys(self, asset_key):
        start = datetime(2026, 1, 1)
        return [(start + timedelta(days=i)).date().isoformat() for i in range(30)]

    def partition_ranges(self, asset_key):
        return [
            {"startKey": "2026-01-01", "endKey": "2026-01-20", "status": "MATERIALIZED"},
            {"startKey": "2026-01-21", "endKey": "2026-01-21", "status": "FAILED"},
        ]

    def launch_backfill(self, asset_keys, partition_names=None, all_partitions=False, title=None, tags=None):
        return "bf-1"

    def backfills(self, limit=20):
        return [
            {
                "id": "bf-1",
                "status": "REQUESTED",
                "title": "Backfill",
                "numPartitions": 3,
                "timestamp": NOW,
                "endTimestamp": None,
                "user": "alice",
                "fromFailure": False,
                "assets": ["raw/orders"],
                "partitionNames": ["2026-01-01"],
                "error": None,
            }
        ]

    def cancel_backfill(self, backfill_id):
        return None


class FakeDB:
    def __init__(self):
        self.pipelines, self.terms, self.connections, self.incidents, self.audit_rows = {}, {}, {}, {}, []
        self.save_term(
            {
                "term": "Commande",
                "definition": "Achat d'un client",
                "synonyms": ["order"],
                "related_assets": ["marts/fct_orders"],
            },
            "system",
        )

    def ping(self):
        return True

    def _stamp(self, data, user):
        now = datetime.now(UTC)
        return {**data, "created_by": user, "updated_by": user, "created_at": now, "updated_at": now}

    def list_pipelines(self):
        return list(self.pipelines.values())

    def save_pipeline(self, data, username):
        from datahub_ui.db.repository import PIPELINE_NAME_RE, ValidationError

        if not PIPELINE_NAME_RE.match(data.get("name", "")):
            raise ValidationError("Nom invalide")
        self.pipelines[data["name"]] = self._stamp({"timezone": "Europe/Paris", "tags": {}, **data}, username)
        return self.pipelines[data["name"]]

    def delete_pipeline(self, name):
        return self.pipelines.pop(name, None) is not None

    def list_terms(self):
        return list(self.terms.values())

    def save_term(self, data, username):
        row = self._stamp({"owner": "", "synonyms": [], "related_assets": [], **data}, username)
        self.terms[data["term"]] = row
        return row

    def delete_term(self, term):
        return self.terms.pop(term, None) is not None

    def list_connections(self):
        return list(self.connections.values())

    def save_connection(self, data, username):
        self.connections[data["name"]] = self._stamp(data, username)
        return self.connections[data["name"]]

    def delete_connection(self, name):
        return self.connections.pop(name, None) is not None

    def list_incidents(self, status=None, limit=200):
        return [i for i in self.incidents.values() if not status or i["status"] == status]

    def record_incident(self, run_id, job_name, category, severity, message):
        if run_id in self.incidents:
            return False
        self.incidents[run_id] = {
            "id": len(self.incidents) + 1,
            "run_id": run_id,
            "job_name": job_name,
            "category": category,
            "severity": severity,
            "message": message,
            "status": "open",
            "assignee": None,
            "comment": "",
            "created_at": datetime.now(UTC),
        }
        return True

    def update_incident(self, incident_id, status, assignee, comment):
        for inc in self.incidents.values():
            if inc["id"] == incident_id:
                inc.update(status=status, assignee=assignee, comment=comment)
                return inc
        return None

    def audit(self, username, roles, action, target="", details=None, success=True):
        self.audit_rows.insert(
            0,
            {
                "ts": datetime.now(UTC),
                "username": username,
                "roles": roles,
                "action": action,
                "target": target,
                "details": details or {},
                "success": success,
            },
        )

    def list_audit(self, limit=500, username=None, action=None):
        return [
            r
            for r in self.audit_rows
            if (not username or r["username"] == username) and (not action or r["action"].startswith(action))
        ][:limit]


class FakeLoki:
    def ready(self):
        return True

    def labels(self, label="container"):
        return ["datahub-dagster-code", "datahub-ui"]

    def query_range(self, logql, minutes=60, limit=500, direction="backward"):
        return [{"ts": NOW - i, "line": f"INFO line {i}", "label_container": "datahub-dagster-code"} for i in range(5)]


class FakePrometheus:
    def ready(self):
        return True

    def query(self, promql):
        return [{"labels": {"name": "datahub-dagster-code"}, "value": 12.5}]

    def scalar(self, promql, default=None):
        return 42.0

    def query_range(self, promql, minutes=60, step="60s"):
        return [{"ts": NOW - i * 60, "series": "cpu", "value": 10.0 + i} for i in range(10)]

    def alerts(self):
        return []

    def targets(self):
        return [{"job": "prometheus", "instance": "localhost:9090", "health": "up", "last_error": ""}]


class FakeKeycloak:
    configured = True

    def __init__(self):
        self.roles = {"u1": ["ADMIN"], "u2": ["ANALYST"]}

    def ready(self):
        return True

    def users(self, search="", max_results=200):
        return [
            {"id": "u1", "username": "admin", "email": "admin@example.com", "enabled": True},
            {"id": "u2", "username": "analyst", "email": "analyst@example.com", "enabled": True},
        ]

    def user_realm_roles(self, user_id):
        return [*self.roles.get(user_id, []), "default-roles-datahub"]

    def user_direct_roles(self, user_id):
        return self.roles.get(user_id, [])

    def set_datahub_roles(self, user_id, roles):
        self.roles[user_id] = sorted(roles)

    def set_enabled(self, user_id, enabled):
        return None

    def logout_user(self, user_id):
        return None

    def groups(self):
        return [{"path": "/admins", "subGroupCount": 0}, {"path": "/analysts", "subGroupCount": 0}]

    def client_sessions_count(self, client_id="datahub-ui"):
        return 3


_SINGLETON: dict = {}


def fake_clients() -> dict:
    if not _SINGLETON:
        _SINGLETON.update(
            dagster=FakeDagster(), db=FakeDB(), loki=FakeLoki(), prometheus=FakePrometheus(), keycloak=FakeKeycloak()
        )
    return _SINGLETON
