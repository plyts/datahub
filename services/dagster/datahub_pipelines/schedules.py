"""Schedules (cron). All times are expressed in DATAHUB_TIMEZONE (Europe/Paris by default)."""

from __future__ import annotations

import dagster as dg

from datahub_pipelines.jobs import daily_partition_job, dbt_marts_job
from datahub_pipelines.settings import get_settings

_tz = get_settings().timezone

# Runs every day at 05:30 for the partition of the previous day.
daily_partition_schedule = dg.build_schedule_from_partitioned_job(
    daily_partition_job,
    name="daily_partition_schedule",
    hour_of_day=5,
    minute_of_hour=30,
    default_status=dg.DefaultScheduleStatus.RUNNING,
    description="Chaque jour à 05:30 : partition J-1 (ingestion + dbt).",
)

weekly_marts_rebuild_schedule = dg.ScheduleDefinition(
    name="weekly_marts_rebuild_schedule",
    job=dbt_marts_job,
    cron_schedule="0 3 * * 0",
    execution_timezone=_tz,
    default_status=dg.DefaultScheduleStatus.STOPPED,
    description="Dimanche 03:00 : reconstruction complète des couches non partitionnées.",
)

STATIC_SCHEDULES = [daily_partition_schedule, weekly_marts_rebuild_schedule]
