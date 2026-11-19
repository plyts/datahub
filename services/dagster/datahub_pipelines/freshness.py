"""Freshness expectations (evaluated continuously by the Dagster daemon, `freshness.enabled` in dagster.yaml)."""

from datetime import timedelta

import dagster as dg

from datahub_pipelines.settings import get_settings

_tz = get_settings().timezone

# Raw data of day D-1 must be loaded every day before 09:00.
RAW_DAILY = dg.FreshnessPolicy.cron(deadline_cron="0 9 * * *", lower_bound_delta=timedelta(hours=24), timezone=_tz)
# Business marts must have been rebuilt within the last 26 hours (warning after 25 hours).
MARTS = dg.FreshnessPolicy.time_window(fail_window=timedelta(hours=26), warn_window=timedelta(hours=25))


def policy_for(asset_key: dg.AssetKey) -> dg.FreshnessPolicy | None:
    layer = asset_key.path[0] if asset_key.path else ""
    if layer == "raw" and asset_key.path[-1] != "landing_files":
        return RAW_DAILY
    if layer == "marts":
        return MARTS
    return None
