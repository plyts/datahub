"""Thin client for the Dagster OSS GraphQL API (webserver).

Every operation used by the Data Hub lives here, so that the queries can be
validated against a real Dagster instance by the integration tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

UI_TAGS = {"datahub/source": "datahub-ui"}


class DagsterError(RuntimeError):
    """Raised when the GraphQL API returns an error or an unexpected result type."""


METADATA_FRAGMENT = """
fragment Meta on MetadataEntry {
  label
  __typename
  ... on IntMetadataEntry { intValue }
  ... on FloatMetadataEntry { floatValue }
  ... on TextMetadataEntry { text }
  ... on JsonMetadataEntry { jsonString }
  ... on PathMetadataEntry { path }
  ... on BoolMetadataEntry { boolValue }
  ... on TableSchemaMetadataEntry { schema { columns { name type description } } }
}
"""

RUN_FIELDS = """
  runId status jobName creationTime startTime endTime updateTime parentRunId rootRunId canTerminate
  tags { key value }
"""


def metadata_to_dict(entries: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Convert GraphQL metadata entries into a plain {label: value} dict."""
    out: dict[str, Any] = {}
    for entry in entries or []:
        label = entry.get("label")
        typename = entry.get("__typename")
        if typename == "IntMetadataEntry":
            value: Any = entry.get("intValue")
        elif typename == "FloatMetadataEntry":
            value = entry.get("floatValue")
        elif typename == "JsonMetadataEntry":
            try:
                value = json.loads(entry.get("jsonString") or "null")
            except ValueError:
                value = entry.get("jsonString")
        elif typename == "TableSchemaMetadataEntry":
            value = [(c["name"], c["type"]) for c in (entry.get("schema") or {}).get("columns", [])]
        elif typename == "BoolMetadataEntry":
            value = entry.get("boolValue")
        elif typename == "PathMetadataEntry":
            value = entry.get("path")
        else:
            value = entry.get("text")
        out[label] = value
    return out


def tags_to_dict(tags: list[dict[str, str]] | None) -> dict[str, str]:
    return {t["key"]: t["value"] for t in tags or []}


def key_to_str(key: list[str] | dict) -> str:
    path = key["path"] if isinstance(key, dict) else key
    return "/".join(path)


def str_to_key(value: str) -> list[str]:
    return [part for part in value.split("/") if part]


@dataclass
class DagsterClient:
    url: str
    location: str = "datahub"
    repository: str = "__repository__"
    timeout: float = 20.0
    session: requests.Session | None = None

    # ------------------------------------------------------------------ core
    def _post(self, query: str, variables: dict | None = None) -> dict[str, Any]:
        http = self.session or requests
        try:
            response = http.post(self.url, json={"query": query, "variables": variables or {}}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise DagsterError(f"Dagster injoignable ({self.url}) : {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise DagsterError(f"Réponse invalide de Dagster (HTTP {response.status_code})") from exc
        if payload.get("errors"):
            raise DagsterError("; ".join(e.get("message", str(e)) for e in payload["errors"]))
        return payload["data"]

    @staticmethod
    def _expect(result: dict, typename: str, what: str) -> dict:
        if not result or result.get("__typename") != typename:
            message = (result or {}).get("message") or (result or {}).get("__typename") or "réponse vide"
            raise DagsterError(f"{what} : {message}")
        return result

    @property
    def repo_selector(self) -> dict[str, str]:
        return {"repositoryLocationName": self.location, "repositoryName": self.repository}

    def job_selector(self, job_name: str) -> dict[str, str]:
        return {**self.repo_selector, "jobName": job_name}

    # ---------------------------------------------------------- health / workspace
    def version(self) -> str:
        return self._post("query { version }")["version"]

    def ping(self) -> bool:
        try:
            self.version()
            return True
        except DagsterError:
            return False

    def locations(self) -> list[dict]:
        data = self._post(
            """query { workspaceOrError { __typename ... on Workspace { locationEntries {
                 name loadStatus updatedTimestamp
                 locationOrLoadError { __typename ... on PythonError { message } } } } } }"""
        )["workspaceOrError"]
        entries = self._expect(data, "Workspace", "Workspace")["locationEntries"]
        return [
            {
                "name": e["name"],
                "status": e["loadStatus"],
                "updated": e["updatedTimestamp"],
                "error": (e["locationOrLoadError"] or {}).get("message"),
            }
            for e in entries
        ]

    def daemon_health(self) -> list[dict]:
        data = self._post(
            """query { instance { daemonHealth { allDaemonStatuses {
                 daemonType healthy required lastHeartbeatTime } } } }"""
        )
        return data["instance"]["daemonHealth"]["allDaemonStatuses"]

    def reload_location(self) -> str:
        data = self._post(
            """mutation($name: String!) { reloadRepositoryLocation(repositoryLocationName: $name) {
                 __typename ... on WorkspaceLocationEntry { name loadStatus
                   locationOrLoadError { __typename ... on PythonError { message } } }
                 ... on PythonError { message } ... on UnauthorizedError { message } } }""",
            {"name": self.location},
        )["reloadRepositoryLocation"]
        entry = self._expect(data, "WorkspaceLocationEntry", "Rechargement de la code location")
        error = (entry.get("locationOrLoadError") or {}).get("message")
        if error:
            raise DagsterError(f"Code location en erreur : {error}")
        return entry["loadStatus"]

    # ------------------------------------------------------------------ jobs / runs
    def jobs(self) -> list[dict]:
        data = self._post(
            """query($s: RepositorySelector!) { repositoryOrError(repositorySelector: $s) {
                 __typename ... on Repository { jobs { name description isAssetJob tags { key value } } }
                 ... on PythonError { message } ... on RepositoryNotFoundError { message } } }""",
            {"s": self.repo_selector},
        )["repositoryOrError"]
        jobs = self._expect(data, "Repository", "Repository")["jobs"]
        return [{**j, "tags": tags_to_dict(j.get("tags"))} for j in jobs if not j["name"].startswith("__ASSET_JOB")]

    def runs(
        self,
        limit: int = 50,
        job_name: str | None = None,
        statuses: list[str] | None = None,
        created_after: float | None = None,
        tags: dict[str, str] | None = None,
    ) -> list[dict]:
        run_filter: dict[str, Any] = {}
        if job_name:
            run_filter["pipelineName"] = job_name
        if statuses:
            run_filter["statuses"] = statuses
        if created_after:
            run_filter["createdAfter"] = created_after
        if tags:
            run_filter["tags"] = [{"key": k, "value": v} for k, v in tags.items()]
        data = self._post(
            f"""query($f: RunsFilter, $l: Int) {{ runsOrError(filter: $f, limit: $l) {{
                 __typename ... on Runs {{ results {{ {RUN_FIELDS} }} }} ... on PythonError {{ message }} }} }}""",
            {"f": run_filter or None, "l": limit},
        )["runsOrError"]
        runs = self._expect(data, "Runs", "Runs")["results"]
        for run in runs:
            run["tags"] = tags_to_dict(run["tags"])
            run["duration"] = (run["endTime"] - run["startTime"]) if run["endTime"] and run["startTime"] else None
        return runs

    def run(self, run_id: str) -> dict:
        data = self._post(
            f"""query($id: ID!) {{ runOrError(runId: $id) {{ __typename
                 ... on Run {{ {RUN_FIELDS} runConfigYaml stepStats {{ stepKey status startTime endTime }} }}
                 ... on RunNotFoundError {{ message }} ... on PythonError {{ message }} }} }}""",
            {"id": run_id},
        )["runOrError"]
        run = self._expect(data, "Run", f"Run {run_id}")
        run["tags"] = tags_to_dict(run["tags"])
        return run

    def run_events(
        self, run_id: str, after_cursor: str | None = None, limit: int = 2000
    ) -> tuple[list[dict], str, bool]:
        data = self._post(
            """query($id: ID!, $c: String, $l: Int) { logsForRun(runId: $id, afterCursor: $c, limit: $l) {
                 __typename
                 ... on EventConnection { cursor hasMore events { __typename
                    ... on MessageEvent { message timestamp level stepKey eventType }
                    ... on ExecutionStepFailureEvent { error { message className stack } }
                    ... on RunFailureEvent { error { message className stack } } } }
                 ... on RunNotFoundError { message } ... on PythonError { message } } }""",
            {"id": run_id, "c": after_cursor, "l": limit},
        )["logsForRun"]
        conn = self._expect(data, "EventConnection", f"Logs du run {run_id}")
        return conn["events"], conn["cursor"], conn["hasMore"]

    def run_errors(self, run_id: str) -> list[dict]:
        events, _, _ = self.run_events(run_id)
        return [e for e in events if e.get("error") or e.get("level") == "ERROR"]

    def launch_job(
        self,
        job_name: str,
        run_config: dict | None = None,
        tags: dict[str, str] | None = None,
        partition_key: str | None = None,
    ) -> str:
        all_tags = {**UI_TAGS, **(tags or {})}
        if partition_key:
            all_tags["dagster/partition"] = partition_key
        data = self._post(
            """mutation($p: ExecutionParams!) { launchRun(executionParams: $p) { __typename
                 ... on LaunchRunSuccess { run { runId } }
                 ... on PythonError { message } ... on RunConfigValidationInvalid { errors { message } }
                 ... on PipelineNotFoundError { message } ... on InvalidSubsetError { message }
                 ... on UnauthorizedError { message } ... on RunConflict { message } } }""",
            {
                "p": {
                    "selector": self.job_selector(job_name),
                    "runConfigData": run_config or {},
                    "executionMetadata": {"tags": [{"key": k, "value": v} for k, v in all_tags.items()]},
                }
            },
        )["launchRun"]
        if data.get("__typename") == "RunConfigValidationInvalid":
            raise DagsterError("Configuration invalide : " + "; ".join(e["message"] for e in data["errors"]))
        return self._expect(data, "LaunchRunSuccess", f"Lancement de {job_name}")["run"]["runId"]

    def reexecute(self, run_id: str, strategy: str = "FROM_FAILURE", tags: dict[str, str] | None = None) -> str:
        data = self._post(
            """mutation($r: ReexecutionParams) { launchRunReexecution(reexecutionParams: $r) { __typename
                 ... on LaunchRunSuccess { run { runId } } ... on PythonError { message }
                 ... on UnauthorizedError { message } ... on RunConflict { message } } }""",
            {
                "r": {
                    "parentRunId": run_id,
                    "strategy": strategy,
                    "extraTags": [{"key": k, "value": v} for k, v in {**UI_TAGS, **(tags or {})}.items()],
                    "useParentRunTags": True,
                }
            },
        )["launchRunReexecution"]
        return self._expect(data, "LaunchRunSuccess", f"Relance du run {run_id}")["run"]["runId"]

    def terminate(self, run_id: str) -> None:
        data = self._post(
            """mutation($id: String!) { terminateRun(runId: $id) { __typename
                 ... on TerminateRunFailure { message } ... on PythonError { message }
                 ... on RunNotFoundError { message } ... on UnauthorizedError { message } } }""",
            {"id": run_id},
        )["terminateRun"]
        self._expect(data, "TerminateRunSuccess", f"Arrêt du run {run_id}")

    # ------------------------------------------------------------ schedules / sensors
    def schedules(self, future_ticks: int = 5) -> list[dict]:
        data = self._post(
            """query($s: RepositorySelector!, $n: Int) { schedulesOrError(repositorySelector: $s) { __typename
                 ... on Schedules { results { name cronSchedule executionTimezone pipelineName description
                   scheduleState { id selectorId status }
                   futureTicks(limit: $n) { results { timestamp } } } }
                 ... on PythonError { message } ... on RepositoryNotFoundError { message } } }""",
            {"s": self.repo_selector, "n": future_ticks},
        )["schedulesOrError"]
        out = []
        for s in self._expect(data, "Schedules", "Schedules")["results"]:
            out.append(
                {
                    "name": s["name"],
                    "cron": s["cronSchedule"],
                    "timezone": s["executionTimezone"],
                    "job": s["pipelineName"],
                    "description": s["description"],
                    "status": s["scheduleState"]["status"],
                    "state_id": s["scheduleState"]["id"],
                    "next_ticks": [t["timestamp"] for t in s["futureTicks"]["results"]],
                }
            )
        return out

    def set_schedule(self, name: str, running: bool) -> str:
        if running:
            data = self._post(
                """mutation($s: ScheduleSelector!) { startSchedule(scheduleSelector: $s) { __typename
                     ... on ScheduleStateResult { scheduleState { status } }
                     ... on PythonError { message } ... on UnauthorizedError { message }
                     ... on ScheduleNotFoundError { message } } }""",
                {"s": {**self.repo_selector, "scheduleName": name}},
            )["startSchedule"]
        else:
            state_id = next((s["state_id"] for s in self.schedules(0) if s["name"] == name), None)
            if not state_id:
                raise DagsterError(f"Schedule inconnu : {name}")
            data = self._post(
                """mutation($id: String) { stopRunningSchedule(id: $id) { __typename
                     ... on ScheduleStateResult { scheduleState { status } }
                     ... on PythonError { message } ... on UnauthorizedError { message } } }""",
                {"id": state_id},
            )["stopRunningSchedule"]
        return self._expect(data, "ScheduleStateResult", f"Schedule {name}")["scheduleState"]["status"]

    def sensors(self) -> list[dict]:
        data = self._post(
            """query($s: RepositorySelector!) { sensorsOrError(repositorySelector: $s) { __typename
                 ... on Sensors { results { name description sensorType minIntervalSeconds
                   targets { pipelineName }
                   sensorState { id selectorId status ticks(limit: 1) { status timestamp } } } }
                 ... on PythonError { message } ... on RepositoryNotFoundError { message } } }""",
            {"s": self.repo_selector},
        )["sensorsOrError"]
        out = []
        for s in self._expect(data, "Sensors", "Sensors")["results"]:
            last_tick = (s["sensorState"]["ticks"] or [{}])[0]
            out.append(
                {
                    "name": s["name"],
                    "description": s["description"],
                    "type": s["sensorType"],
                    "interval_s": s["minIntervalSeconds"],
                    "targets": [t["pipelineName"] for t in s["targets"] or []],
                    "status": s["sensorState"]["status"],
                    "state_id": s["sensorState"]["id"],
                    "last_tick_status": last_tick.get("status"),
                    "last_tick": last_tick.get("timestamp"),
                }
            )
        return out

    def set_sensor(self, name: str, running: bool) -> str:
        if running:
            data = self._post(
                """mutation($s: SensorSelector!) { startSensor(sensorSelector: $s) { __typename
                     ... on Sensor { sensorState { status } } ... on PythonError { message }
                     ... on UnauthorizedError { message } ... on SensorNotFoundError { message } } }""",
                {"s": {**self.repo_selector, "sensorName": name}},
            )["startSensor"]
            return self._expect(data, "Sensor", f"Sensor {name}")["sensorState"]["status"]
        state_id = next((s["state_id"] for s in self.sensors() if s["name"] == name), None)
        if not state_id:
            raise DagsterError(f"Sensor inconnu : {name}")
        data = self._post(
            """mutation($id: String) { stopSensor(id: $id) { __typename
                 ... on StopSensorMutationResult { instigationState { status } }
                 ... on PythonError { message } ... on UnauthorizedError { message } } }""",
            {"id": state_id},
        )["stopSensor"]
        return self._expect(data, "StopSensorMutationResult", f"Sensor {name}")["instigationState"]["status"]

    # ------------------------------------------------------------------- assets
    def assets(self) -> list[dict]:
        data = self._post(
            """query { assetNodes { assetKey { path } groupName description kinds isPartitioned
                 opName pools hasAssetChecks
                 owners { __typename ... on UserAssetOwner { email } ... on TeamAssetOwner { team } }
                 dependencyKeys { path } dependedByKeys { path }
                 partitionStats { numMaterialized numPartitions numFailed numMaterializing }
                 freshnessStatusInfo { freshnessStatus }
                 assetMaterializations(limit: 1) { timestamp runId partition } } }"""
        )["assetNodes"]
        out = []
        for a in data:
            last = (a.get("assetMaterializations") or [None])[0]
            owners = [o.get("email") or f"team:{o.get('team')}" for o in a.get("owners") or []]
            out.append(
                {
                    "key": key_to_str(a["assetKey"]),
                    "path": a["assetKey"]["path"],
                    "group": a["groupName"],
                    "description": a["description"] or "",
                    "kinds": a["kinds"],
                    "partitioned": a["isPartitioned"],
                    "op": a["opName"],
                    "has_checks": a["hasAssetChecks"],
                    "owners": owners,
                    "deps": [key_to_str(k) for k in a["dependencyKeys"]],
                    "dependents": [key_to_str(k) for k in a["dependedByKeys"]],
                    "partition_stats": a.get("partitionStats"),
                    "freshness": (a.get("freshnessStatusInfo") or {}).get("freshnessStatus"),
                    "last_materialization": float(last["timestamp"]) / 1000 if last else None,
                    "last_run_id": last["runId"] if last else None,
                }
            )
        return out

    def materializations(self, asset_key: str, limit: int = 50) -> list[dict]:
        data = self._post(
            METADATA_FRAGMENT
            + """query($k: AssetKeyInput!, $l: Int) { assetNodeOrError(assetKey: $k) { __typename
                 ... on AssetNode { assetMaterializations(limit: $l) {
                   runId timestamp partition stepKey metadataEntries { ...Meta } } }
                 ... on AssetNotFoundError { message } } }""",
            {"k": {"path": str_to_key(asset_key)}, "l": limit},
        )["assetNodeOrError"]
        node = self._expect(data, "AssetNode", f"Asset {asset_key}")
        return [
            {
                "run_id": m["runId"],
                "timestamp": float(m["timestamp"]) / 1000,
                "partition": m["partition"],
                "metadata": metadata_to_dict(m["metadataEntries"]),
            }
            for m in node["assetMaterializations"]
        ]

    def asset_checks(self, asset_key: str) -> list[dict]:
        data = self._post(
            """query($k: AssetKeyInput!) { assetNodeOrError(assetKey: $k) { __typename
                 ... on AssetNode { assetChecksOrError { __typename ... on AssetChecks { checks {
                   name description blocking
                   executionForLatestMaterialization { runId status timestamp
                     evaluation { severity success description } } } } } }
                 ... on AssetNotFoundError { message } } }""",
            {"k": {"path": str_to_key(asset_key)}},
        )["assetNodeOrError"]
        node = self._expect(data, "AssetNode", f"Asset {asset_key}")
        checks = (node.get("assetChecksOrError") or {}).get("checks") or []
        out = []
        for c in checks:
            ex = c.get("executionForLatestMaterialization") or {}
            ev = ex.get("evaluation") or {}
            out.append(
                {
                    "asset": asset_key,
                    "name": c["name"],
                    "description": c["description"] or "",
                    "blocking": c["blocking"],
                    "status": ex.get("status"),
                    "severity": ev.get("severity"),
                    "timestamp": ex.get("timestamp"),
                    "run_id": ex.get("runId"),
                }
            )
        return out

    def check_history(self, asset_key: str, check_name: str, limit: int = 30) -> list[dict]:
        data = self._post(
            """query($k: AssetKeyInput!, $c: String!, $l: Int!) {
                 assetCheckExecutions(assetKey: $k, checkName: $c, limit: $l) {
                   runId status timestamp partition evaluation { severity success } } }""",
            {"k": {"path": str_to_key(asset_key)}, "c": check_name, "l": limit},
        )["assetCheckExecutions"]
        return [
            {
                "run_id": e["runId"],
                "status": e["status"],
                "timestamp": e["timestamp"],
                "partition": e["partition"],
                "severity": (e.get("evaluation") or {}).get("severity"),
            }
            for e in data
        ]

    def partition_ranges(self, asset_key: str) -> list[dict]:
        data = self._post(
            """query($k: AssetKeyInput!) { assetNodeOrError(assetKey: $k) { __typename
                 ... on AssetNode { partitionKeyConnection(limit: 1, ascending: true) { results }
                   assetPartitionStatuses { __typename
                     ... on TimePartitionStatuses { ranges { startKey endKey status } }
                     ... on DefaultPartitionStatuses { materializedPartitions failedPartitions } } }
                 ... on AssetNotFoundError { message } } }""",
            {"k": {"path": str_to_key(asset_key)}},
        )["assetNodeOrError"]
        node = self._expect(data, "AssetNode", f"Asset {asset_key}")
        statuses = node.get("assetPartitionStatuses") or {}
        return statuses.get("ranges") or []

    def partition_keys(self, asset_key: str) -> list[str]:
        data = self._post(
            """query($k: AssetKeyInput!) { assetNodeOrError(assetKey: $k) { __typename
                 ... on AssetNode { partitionKeys } ... on AssetNotFoundError { message } } }""",
            {"k": {"path": str_to_key(asset_key)}},
        )["assetNodeOrError"]
        return self._expect(data, "AssetNode", f"Asset {asset_key}")["partitionKeys"]

    # ----------------------------------------------------------------- backfills
    def launch_backfill(
        self,
        asset_keys: list[str],
        partition_names: list[str] | None = None,
        all_partitions: bool = False,
        title: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> str:
        params: dict[str, Any] = {
            "assetSelection": [{"path": str_to_key(k)} for k in asset_keys],
            "tags": [{"key": k, "value": v} for k, v in {**UI_TAGS, **(tags or {})}.items()],
            "title": title,
        }
        if all_partitions:
            params["allPartitions"] = True
        else:
            params["partitionNames"] = partition_names or []
        data = self._post(
            """mutation($p: LaunchBackfillParams!) { launchPartitionBackfill(backfillParams: $p) { __typename
                 ... on LaunchBackfillSuccess { backfillId }
                 ... on PythonError { message } ... on PartitionKeysNotFoundError { message }
                 ... on UnauthorizedError { message } ... on InvalidSubsetError { message } } }""",
            {"p": params},
        )["launchPartitionBackfill"]
        return self._expect(data, "LaunchBackfillSuccess", "Backfill")["backfillId"]

    def backfills(self, limit: int = 20) -> list[dict]:
        data = self._post(
            """query($l: Int) { partitionBackfillsOrError(limit: $l) { __typename
                 ... on PartitionBackfills { results { id status title numPartitions timestamp endTimestamp
                   user fromFailure assetSelection { path } partitionNames
                   error { message } } }
                 ... on PythonError { message } } }""",
            {"l": limit},
        )["partitionBackfillsOrError"]
        out = []
        for b in self._expect(data, "PartitionBackfills", "Backfills")["results"]:
            b["assets"] = [key_to_str(k) for k in b.pop("assetSelection") or []]
            b["error"] = (b.get("error") or {}).get("message")
            out.append(b)
        return out

    def cancel_backfill(self, backfill_id: str) -> None:
        data = self._post(
            """mutation($id: String!) { cancelPartitionBackfill(backfillId: $id) { __typename
                 ... on PythonError { message } ... on UnauthorizedError { message } } }""",
            {"id": backfill_id},
        )["cancelPartitionBackfill"]
        self._expect(data, "CancelBackfillSuccess", f"Annulation du backfill {backfill_id}")
