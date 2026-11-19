import pytest

from datahub_pipelines.custom_pipelines import (
    PipelineSpec,
    build_custom_pipelines,
    load_specs_from_db,
    validate_spec,
)


@pytest.fixture(scope="module")
def assets_and_resources():
    from datahub_pipelines.definitions import ALL_ASSETS, DBT_ASSETS, RESOURCES

    return ALL_ASSETS, DBT_ASSETS, RESOURCES


def _build(specs, assets_and_resources):
    assets, dbt_assets_defs, resources = assets_and_resources
    return build_custom_pipelines(specs, assets, dbt_assets_defs, resources)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        (PipelineSpec(name="Bad Name", selection_type="dagster", selection="group:staging"), "nom invalide"),
        (PipelineSpec(name="ok_name", selection_type="sql", selection="x"), "type de sélection"),
        (PipelineSpec(name="ok_name", selection_type="dagster", selection="  "), "vide"),
        (PipelineSpec(name="ok_name", selection_type="dagster", selection="group:x", cron="99 * * * *"), "cron"),
    ],
)
def test_validate_spec_rejects_invalid(spec, message):
    with pytest.raises(ValueError, match=message):
        validate_spec(spec)


def test_dagster_selection_builds_job_and_schedule(assets_and_resources):
    spec = PipelineSpec(
        name="finance_only", selection_type="dagster", selection="group:marts_finance", cron="0 8 * * 1-5"
    )
    result = _build([spec], assets_and_resources)
    assert result.errors == {}
    assert [j.name for j in result.jobs] == ["custom_finance_only"]
    assert [s.name for s in result.schedules] == ["custom_finance_only_schedule"]


def test_dbt_selection(assets_and_resources):
    spec = PipelineSpec(name="staging_layer", selection_type="dbt", selection="tag:staging")
    result = _build([spec], assets_and_resources)
    assert result.errors == {}
    assert result.jobs and not result.schedules


def test_partitioned_selection_gets_partitioned_schedule(assets_and_resources):
    spec = PipelineSpec(
        name="raw_orders_only", selection_type="dagster", selection='key:"raw/orders"', cron="15 6 * * *"
    )
    result = _build([spec], assets_and_resources)
    assert result.errors == {}
    assert len(result.schedules) == 1


def test_invalid_selection_is_reported_not_raised(assets_and_resources):
    specs = [
        PipelineSpec(name="nothing_matches", selection_type="dagster", selection="group:does_not_exist"),
        PipelineSpec(name="syntax_error", selection_type="dagster", selection="group:(("),
        PipelineSpec(name="valid_one", selection_type="dagster", selection="group:staging"),
    ]
    result = _build(specs, assets_and_resources)
    assert set(result.errors) == {"nothing_matches", "syntax_error"}
    assert [j.name for j in result.jobs] == ["custom_valid_one"]


def test_disabled_specs_are_ignored(assets_and_resources):
    spec = PipelineSpec(name="disabled_one", selection_type="dagster", selection="group:staging", enabled=False)
    result = _build([spec], assets_and_resources)
    assert result.jobs == [] and result.errors == {}


def test_unreachable_database_returns_no_spec():
    assert load_specs_from_db("") == []
    assert load_specs_from_db("postgresql://nobody:x@127.0.0.1:1/none", timeout=1) == []


def test_partitioned_schedule_requests_last_partition(assets_and_resources):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import dagster as dg

    spec = PipelineSpec(
        name="raw_orders_tick", selection_type="dagster", selection='key:"raw/orders"', cron="15 6 * * *"
    )
    assets, _, resources = assets_and_resources
    result = _build([spec], assets_and_resources)
    repo = dg.Definitions(
        assets=assets, jobs=result.jobs, schedules=result.schedules, resources=resources
    ).get_repository_def()
    schedule = repo.get_schedule_def("custom_raw_orders_tick_schedule")
    tick = datetime(2026, 4, 10, 6, 15, tzinfo=ZoneInfo("Europe/Paris"))
    context = dg.build_schedule_context(scheduled_execution_time=tick, repository_def=repo)
    request = schedule.evaluate_tick(context).run_requests[0]
    assert request.partition_key == "2026-04-09"
