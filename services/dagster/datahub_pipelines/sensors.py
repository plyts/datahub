"""Event-driven triggers (sensors)."""

from pathlib import Path

import dagster as dg

from datahub_pipelines.jobs import daily_partition_job, landing_files_job
from datahub_pipelines.notifications import FailureNotice, notify
from datahub_pipelines.ops.docs import dbt_docs_job
from datahub_pipelines.settings import get_settings


def pending_landing_files(landing_dir: str | Path) -> list[Path]:
    """CSV files waiting in the landing directory (processed files are moved to `_processed/`)."""
    root = Path(landing_dir)
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*.csv") if p.is_file())


@dg.sensor(
    job=landing_files_job,
    minimum_interval_seconds=60,
    default_status=dg.DefaultSensorStatus.RUNNING,
    description="Déclenche le chargement de chaque nouveau fichier CSV déposé dans le répertoire d'atterrissage.",
)
def landing_file_sensor(context: dg.SensorEvaluationContext):
    files = pending_landing_files(get_settings().landing_dir)
    if not files:
        return dg.SkipReason("Aucun fichier en attente.")
    for path in files:
        stat = path.stat()
        yield dg.RunRequest(
            # run_key de-duplicates: the same file version never triggers twice.
            run_key=f"{path.name}:{int(stat.st_mtime)}:{stat.st_size}",
            run_config={"ops": {"raw__landing_files": {"config": {"files": [str(path)]}}}},
            tags={"datahub/landing_file": path.name},
        )


@dg.run_status_sensor(
    run_status=dg.DagsterRunStatus.SUCCESS,
    monitored_jobs=[daily_partition_job],
    request_job=dbt_docs_job,
    default_status=dg.DefaultSensorStatus.RUNNING,
    description="Après chaque succès du pipeline quotidien : régénère la documentation dbt (catalogue).",
)
def refresh_docs_after_daily_success(context: dg.RunStatusSensorContext):
    return dg.RunRequest(run_key=f"docs-after-{context.dagster_run.run_id}")


@dg.run_failure_sensor(
    name="failure_notification_sensor",
    monitor_all_code_locations=False,
    default_status=dg.DefaultSensorStatus.RUNNING,
    description="Notifie (webhook / e-mail) chaque échec de run.",
)
def failure_notification_sensor(context: dg.RunFailureSensorContext):
    settings = get_settings()
    error = context.failure_event.message if context.failure_event else ""
    notice = FailureNotice(
        job_name=context.dagster_run.job_name,
        run_id=context.dagster_run.run_id,
        error=error,
        env=settings.env,
        public_url=settings.public_url,
    )
    context.log.error(notice.as_text())
    results = notify(settings, notice)
    context.log.info("Notification channels: %s", results or "none configured")


STATIC_SENSORS = [landing_file_sensor, refresh_docs_after_daily_success, failure_notification_sensor]
