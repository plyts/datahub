"""Generate the provisioned Grafana dashboards (python3 build_dashboards.py).

Dashboards are code: edit this file, regenerate, commit both. A test checks that the
committed JSON is in sync and that every PromQL expression is valid.
"""

import json
from pathlib import Path

OUT = Path(__file__).parent / "dashboards"
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
PG = {"type": "grafana-postgresql-datasource", "uid": "datahub-pg"}


class Layout:
    def __init__(self):
        self.x, self.y, self.row_h, self.next_id = 0, 0, 0, 1

    def pos(self, w, h):
        if self.x + w > 24:
            self.x, self.y = 0, self.y + self.row_h
            self.row_h = 0
        gp = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        self.next_id += 1
        return gp, self.next_id - 1


def stat(layout, title, expr, unit="none", w=6, h=4, thresholds=None, description=""):
    gp, pid = layout.pos(w, h)
    steps = thresholds or [{"color": "green", "value": None}]
    return {"id": pid, "type": "stat", "title": title, "description": description, "gridPos": gp, "datasource": PROM,
            "targets": [{"refId": "A", "expr": expr, "datasource": PROM, "instant": True}],
            "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}},
                            "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background"}}


def timeseries(layout, title, targets, unit="none", w=12, h=8, stack=False):
    gp, pid = layout.pos(w, h)
    return {"id": pid, "type": "timeseries", "title": title, "gridPos": gp, "datasource": PROM,
            "targets": [{"refId": chr(65 + i), "expr": e, "legendFormat": lf, "datasource": PROM}
                        for i, (e, lf) in enumerate(targets)],
            "fieldConfig": {"defaults": {"unit": unit, "custom": {"stacking": {"mode": "normal" if stack else "none"},
                                                                  "fillOpacity": 15}}, "overrides": []}}


def table(layout, title, expr, w=12, h=8, rename=None):
    gp, pid = layout.pos(w, h)
    return {"id": pid, "type": "table", "title": title, "gridPos": gp, "datasource": PROM,
            "targets": [{"refId": "A", "expr": expr, "format": "table", "instant": True, "datasource": PROM}],
            "transformations": [{"id": "organize", "options": {"excludeByName": {"Time": True, "__name__": True},
                                                               "renameByName": rename or {}}}]}


def dashboard(uid, title, panels, tags):
    return {"uid": uid, "title": title, "tags": ["datahub", *tags], "timezone": "browser", "schemaVersion": 39,
            "version": 1, "editable": False, "refresh": "1m", "time": {"from": "now-24h", "to": "now"},
            "panels": panels, "templating": {"list": []}, "annotations": {"list": []}}


RED = [{"color": "green", "value": None}, {"color": "red", "value": 1}]
GREEN_IF_ONE = [{"color": "red", "value": None}, {"color": "green", "value": 1}]


def pipelines():
    l = Layout()
    panels = [
        stat(l, "API Dagster", "datahub_dagster_up", thresholds=GREEN_IF_ONE, description="1 = joignable"),
        stat(l, "Runs (24 h)", "sum(datahub_dagster_runs_24h)"),
        stat(l, "Échecs (24 h)", 'sum(datahub_dagster_runs_24h{status="FAILURE"}) or vector(0)', thresholds=RED),
        stat(l, "Runs en file", "datahub_dagster_runs_queued",
             thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 5}]),
        timeseries(l, "Runs par statut (fenêtre 24 h)", [("sum by (status) (datahub_dagster_runs_24h)", "{{status}}")],
                   stack=True),
        timeseries(l, "Durée du dernier run par pipeline", [("datahub_dagster_last_run_duration_seconds", "{{job}}")],
                   unit="s"),
        table(l, "Dernier run par pipeline (1 = succès)", "datahub_dagster_last_run_success",
              rename={"job": "Pipeline", "Value": "Succès"}),
        table(l, "Contrôles qualité en échec", "datahub_dagster_asset_check_passed == 0",
              rename={"asset": "Asset", "check": "Contrôle", "severity": "Sévérité"}),
        table(l, "Fraîcheur des assets (0 OK · 1 warning · 2 dégradé)", "datahub_dagster_asset_freshness",
              rename={"asset": "Asset", "Value": "Fraîcheur"}),
        table(l, "Santé des daemons Dagster", "datahub_dagster_daemon_healthy",
              rename={"daemon": "Daemon", "Value": "Sain"}),
    ]
    return dashboard("datahub-pipelines", "Data Hub — Pipelines & qualité", panels, ["dagster", "dbt"])


def infrastructure():
    l = Layout()
    panels = [
        stat(l, "CPU VM", '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])))', unit="percent",
             thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 75}, {"color": "red", "value": 90}]),
        stat(l, "RAM VM", "100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)", unit="percent",
             thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 75}, {"color": "red", "value": 90}]),
        stat(l, "Disque / utilisé",
             '100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})',
             unit="percent",
             thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 80}, {"color": "red", "value": 90}]),
        stat(l, "Services DOWN", "count(up == 0) or vector(0)", thresholds=RED),
        timeseries(l, "CPU par conteneur", [('sum by (name) (rate(container_cpu_usage_seconds_total{name=~".+"}[5m])) * 100',
                                             "{{name}}")], unit="percent"),
        timeseries(l, "Mémoire par conteneur", [('sum by (name) (container_memory_working_set_bytes{name=~".+"})',
                                                 "{{name}}")], unit="bytes"),
        timeseries(l, "Charge système", [("node_load1", "load 1m"), ("node_load5", "load 5m")]),
        timeseries(l, "Connexions PostgreSQL par base", [("sum by (datname) (pg_stat_database_numbackends)", "{{datname}}")]),
        table(l, "État des cibles de collecte", "up", rename={"job": "Service", "instance": "Instance", "Value": "UP"}),
        timeseries(l, "Requêtes HTTP du point d'entrée (Caddy)",
                   [("sum by (code) (rate(caddy_http_requests_total[5m]))", "HTTP {{code}}")], unit="reqps"),
        timeseries(l, "Latence p95 des appels de l'interface (traces OpenTelemetry)",
                   [("histogram_quantile(0.95, sum by (le, service_name) "
                     "(rate(traces_span_metrics_duration_seconds_bucket[5m])))", "{{service_name}}")], unit="s"),
    ]
    return dashboard("datahub-infrastructure", "Data Hub — Infrastructure", panels, ["infra"])


def logs_audit():
    l = Layout()
    gp1, id1 = l.pos(24, 8)
    gp2, id2 = l.pos(24, 10)
    gp3, id3 = l.pos(24, 10)
    panels = [
        {"id": id1, "type": "timeseries", "title": "Volume de logs ERROR par conteneur", "gridPos": gp1, "datasource": LOKI,
         "targets": [{"refId": "A", "datasource": LOKI,
                      "expr": 'sum by (container) (count_over_time({job="docker", level=~"error|critical|fatal"}[5m]))',
                      "legendFormat": "{{container}}"}]},
        {"id": id2, "type": "logs", "title": "Erreurs récentes (tous services)", "gridPos": gp2, "datasource": LOKI,
         "targets": [{"refId": "A", "datasource": LOKI, "expr": '{job="docker"} |~ "(?i)(error|exception|traceback)"'}],
         "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending"}},
        {"id": id3, "type": "table", "title": "Journal d'audit Data Hub (200 dernières actions)", "gridPos": gp3,
         "datasource": PG,
         "targets": [{"refId": "A", "datasource": PG, "format": "table", "rawQuery": True, "editorMode": "code",
                      "rawSql": "select ts as \"Date\", username as \"Utilisateur\", action as \"Action\", "
                                "target as \"Cible\", success as \"Succès\" from datahub.audit_log "
                                "order by ts desc limit 200"}]},
    ]
    return dashboard("datahub-logs-audit", "Data Hub — Logs & audit", panels, ["loki", "audit"])


DASHBOARDS = {"pipelines.json": pipelines, "infrastructure.json": infrastructure, "logs-audit.json": logs_audit}


def render() -> dict[str, str]:
    return {name: json.dumps(fn(), indent=2, ensure_ascii=False) + "\n" for name, fn in DASHBOARDS.items()}


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, content in render().items():
        (OUT / name).write_text(content, encoding="utf-8")
        print("written", OUT / name)
