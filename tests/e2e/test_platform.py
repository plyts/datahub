"""Platform smoke tests: every service answers behind the entry point and pipelines really run."""

import os
import time

import pytest

from tests.e2e.conftest import PUBLIC_URL, USERS, keycloak_login, new_session


@pytest.fixture(scope="module")
def engineer_session():
    session = new_session()
    final = keycloak_login(session, f"{PUBLIC_URL}/oauth2/start?rd={PUBLIC_URL}/dagster/", USERS["DATA_ENGINEER"])
    assert final.status_code == 200
    return session


def gql(session, query, variables=None):
    response = session.post(f"{PUBLIC_URL}/dagster/graphql", json={"query": query, "variables": variables or {}},
                            timeout=30)
    response.raise_for_status()
    payload = response.json()
    assert "errors" not in payload, payload
    return payload["data"]


def test_streamlit_is_served():
    response = new_session().get(f"{PUBLIC_URL}/", timeout=10)
    assert response.status_code == 200 and "<title>Streamlit</title>" in response.text or "streamlit" in response.text.lower()


def test_code_location_loaded_and_daemons_healthy(engineer_session):
    data = gql(engineer_session, """{ workspaceOrError { ... on Workspace { locationEntries { name loadStatus } } }
                                      instance { daemonHealth { allDaemonStatuses { daemonType healthy required } } } }""")
    assert data["workspaceOrError"]["locationEntries"][0]["loadStatus"] == "LOADED"
    unhealthy = [d["daemonType"] for d in data["instance"]["daemonHealth"]["allDaemonStatuses"]
                 if d["required"] and not d["healthy"]]
    assert unhealthy == []


@pytest.mark.skipif(os.getenv("E2E_RUN_PIPELINE", "1") != "1", reason="E2E_RUN_PIPELINE=0")
def test_daily_pipeline_runs_through_the_platform(engineer_session):
    partition = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 3 * 86400))
    launch = gql(engineer_session, """mutation($p: ExecutionParams!) { launchRun(executionParams: $p) {
                   __typename ... on LaunchRunSuccess { run { runId } } ... on PythonError { message } } }""",
                 {"p": {"selector": {"repositoryLocationName": "datahub", "repositoryName": "__repository__",
                                     "jobName": "daily_partition_job"},
                        "executionMetadata": {"tags": [{"key": "dagster/partition", "value": partition},
                                                       {"key": "datahub/user", "value": "e2e"}]}}})["launchRun"]
    assert launch["__typename"] == "LaunchRunSuccess", launch
    run_id = launch["run"]["runId"]
    deadline = time.time() + 600
    status = None
    while time.time() < deadline:
        status = gql(engineer_session, "query($id: ID!) { runOrError(runId: $id) { ... on Run { status } } }",
                     {"id": run_id})["runOrError"]["status"]
        if status in ("SUCCESS", "FAILURE", "CANCELED"):
            break
        time.sleep(5)
    assert status == "SUCCESS", f"run {run_id} ended {status}"
