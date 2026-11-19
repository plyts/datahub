"""Pipelines created from the Data Hub UI (module 01 - Pipeline Management).

Users do not write Python: they store a *pipeline definition* (name, asset selection,
optional cron) in the Data Hub application database. At load time this code location
turns each definition into a Dagster job (+ schedule). The UI then asks Dagster to
reload the code location so the new pipeline appears immediately.

Two selection languages are supported:
- ``dagster``: Dagster asset selection syntax, e.g. ``group:marts_finance``, ``key:"raw/orders"+``,
  ``tag:layer=raw``, ``kind:dbt and group:staging``;
- ``dbt``: a dbt selector, e.g. ``tag:finance``, ``+agg_daily_revenue``, ``path:models/marts``.

Invalid definitions are skipped (and reported) so they can never break the code location.
"""

from __future__ import annotations

import logging
import operator
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from functools import reduce
from zoneinfo import ZoneInfo

import dagster as dg
from dagster._utils.schedules import is_valid_cron_string  # same validator as ScheduleDefinition
from dagster_dbt import build_dbt_asset_selection

from datahub_pipelines.partitions import daily_partitions

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
JOB_PREFIX = "custom_"
SELECTION_TYPES = ("dagster", "dbt")


@dataclass(frozen=True)
class PipelineSpec:
    name: str
    selection_type: str
    selection: str
    description: str = ""
    cron: str | None = None
    timezone: str = "Europe/Paris"
    enabled: bool = True
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def job_name(self) -> str:
        return f"{JOB_PREFIX}{self.name}"


@dataclass
class BuildResult:
    jobs: list = field(default_factory=list)
    schedules: list = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def validate_spec(spec: PipelineSpec) -> None:
    """Static validation (no asset graph needed). Raises ValueError."""
    if not NAME_RE.match(spec.name):
        raise ValueError("nom invalide : minuscules, chiffres et _ (3 à 63 caractères, commence par une lettre)")
    if spec.selection_type not in SELECTION_TYPES:
        raise ValueError(f"type de sélection inconnu : {spec.selection_type}")
    if not spec.selection.strip():
        raise ValueError("la sélection est vide")
    if spec.cron and not is_valid_cron_string(spec.cron):
        raise ValueError(f"expression cron invalide : {spec.cron}")


def build_selection(spec: PipelineSpec, dbt_assets_defs: Sequence[dg.AssetsDefinition]) -> dg.AssetSelection:
    if spec.selection_type == "dbt":
        # build_dbt_asset_selection works on one dbt multi-asset at a time: union of all of them.
        return reduce(
            operator.or_,
            (build_dbt_asset_selection([d], dbt_select=spec.selection) for d in dbt_assets_defs),
        )
    return dg.AssetSelection.from_string(spec.selection)


def partitioned_schedule(job, spec: PipelineSpec) -> dg.ScheduleDefinition:
    """Cron schedule that requests the last complete daily partition at each tick."""

    def execution_fn(context: dg.ScheduleEvaluationContext):
        tick = context.scheduled_execution_time.astimezone(ZoneInfo(daily_partitions.timezone))
        partition_key = (tick.date() - timedelta(days=1)).isoformat()
        if partition_key < daily_partitions.get_first_partition_key():
            return dg.SkipReason("Aucune partition disponible.")
        return dg.RunRequest(partition_key=partition_key)

    return dg.ScheduleDefinition(
        name=f"{spec.job_name}_schedule",
        job=job,
        cron_schedule=spec.cron,
        execution_timezone=spec.timezone,
        execution_fn=execution_fn,
        default_status=dg.DefaultScheduleStatus.RUNNING,
    )


def build_custom_pipelines(
    specs: Sequence[PipelineSpec],
    assets: Sequence[dg.AssetsDefinition],
    dbt_assets_defs: Sequence[dg.AssetsDefinition],
    resources: dict | None = None,
) -> BuildResult:
    result = BuildResult()
    if not any(spec.enabled for spec in specs):
        return result
    asset_graph = dg.Definitions(assets=list(assets), resources=resources or {}).resolve_asset_graph()
    partitioned_keys = {
        key for key in asset_graph.get_all_asset_keys() if asset_graph.get(key).partitions_def is not None
    }

    for spec in specs:
        if not spec.enabled:
            continue
        try:
            validate_spec(spec)
            selection = build_selection(spec, dbt_assets_defs)
            keys = selection.resolve(asset_graph)
            if not keys:
                raise ValueError("la sélection ne correspond à aucun asset")
            is_partitioned = bool(keys & partitioned_keys)
            job = dg.define_asset_job(
                name=spec.job_name,
                selection=selection,
                description=spec.description or f"Pipeline personnalisé « {spec.name} » (créé depuis le Data Hub).",
                tags={"datahub/kind": "custom", "datahub/pipeline": spec.name, **spec.tags},
            )
            # Resolve now so that an invalid job is reported here instead of breaking the code location.
            dg.Definitions(assets=list(assets), jobs=[job], resources=resources or {}).resolve_job_def(spec.job_name)
            result.jobs.append(job)
            if spec.cron:
                if is_partitioned:
                    schedule = partitioned_schedule(job, spec)
                else:
                    schedule = dg.ScheduleDefinition(
                        name=f"{spec.job_name}_schedule",
                        job=job,
                        cron_schedule=spec.cron,
                        execution_timezone=spec.timezone,
                        default_status=dg.DefaultScheduleStatus.RUNNING,
                    )
                result.schedules.append(schedule)
        except Exception as exc:  # noqa: BLE001 - any invalid definition must be skipped, never fatal
            log.warning("Custom pipeline %r skipped: %s", spec.name, exc)
            result.errors[spec.name] = str(exc)
    return result


def load_specs_from_db(db_url: str, timeout: int = 3) -> list[PipelineSpec]:
    """Read enabled pipeline definitions. Returns [] if the database is unreachable."""
    if not db_url:
        return []
    try:
        import psycopg

        with psycopg.connect(db_url, connect_timeout=timeout) as conn, conn.cursor() as cur:
            cur.execute(
                """
                select name, selection_type, selection, coalesce(description, ''), cron,
                       coalesce(timezone, 'Europe/Paris'), enabled, coalesce(tags, '{}'::jsonb)
                from datahub.pipeline_definitions
                where enabled
                order by name
                """
            )
            return [
                PipelineSpec(
                    name=row[0],
                    selection_type=row[1],
                    selection=row[2],
                    description=row[3],
                    cron=row[4] or None,
                    timezone=row[5],
                    enabled=row[6],
                    tags={str(k): str(v) for k, v in (row[7] or {}).items()},
                )
                for row in cur.fetchall()
            ]
    except Exception as exc:  # noqa: BLE001 - DB down must not prevent the code location from loading
        log.warning("Could not load custom pipelines from the application database: %s", exc)
        return []
