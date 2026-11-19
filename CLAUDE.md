# Data Hub — repository guide

- Services: `services/dagster` (Dagster code location + dbt project on DuckDB), `services/datahub-ui` (Streamlit, 11 modules, Prometheus exporter), infra configs under `services/*`, stack in `docker-compose.yml` (+ `compose/` overrides, `envs/*.env`, secrets in `.env` never committed).
- Python 3.12. Dependencies are locked: edit `requirements.in`, then `uv pip compile requirements.in --python-version 3.12 -o requirements.lock`.
- Tests: `make test` (config + Dagster/dbt + UI), `make test-integration` (live Dagster/PostgreSQL), `make test-e2e` (running stack). Every change ships with tests.
- Lint: `make lint` (ruff, per-service config) and `shellcheck -S warning scripts/*.sh`.
- DuckDB has a single writer: any new asset/op writing to DuckDB must use `pool=DUCKDB_POOL`.
- dbt asset keys are `[schema, model]`; after editing the dbt project, `make dbt-parse` (tests regenerate the manifest automatically).
- Grafana dashboards are generated: edit `services/grafana/build_dashboards.py`, run `make dashboards`.
- Every sensitive UI action: `ctx.user.require(Perm.X)` then `ctx.audit(...)`.
