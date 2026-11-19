"""Data quality checks implemented in Python (dbt tests are added automatically by dagster-dbt).

- completeness: share of NULL emails in raw.customers (blocking);
- volume anomaly: z-score of the latest daily order volume vs. the previous 28 days.

Freshness is declared with `FreshnessPolicy` objects attached to the assets (see freshness.py).
"""

import statistics

import dagster as dg
from dagster_duckdb import DuckDBResource

from datahub_pipelines.assets.ingestion import raw_customers, raw_orders

ANOMALY_Z_THRESHOLD = 3.0
ANOMALY_HISTORY_DAYS = 28
MAX_NULL_EMAIL_RATIO = 0.01


def zscore(value: float, history: list[float]) -> float:
    """Standard score of `value` against `history` (0 when history is too short or flat)."""
    if len(history) < 7:
        return 0.0
    stdev = statistics.pstdev(history)
    if stdev == 0:
        return 0.0
    return (value - statistics.fmean(history)) / stdev


@dg.asset_check(asset=raw_customers, blocking=True, description="Moins de 1 % d'emails NULL dans raw.customers.")
def customers_email_completeness(duckdb: DuckDBResource) -> dg.AssetCheckResult:
    with duckdb.get_connection() as conn:
        total, nulls = conn.execute(
            "select count(*), count(*) filter (where email is null or email = '') from raw.customers"
        ).fetchone()
    ratio = (nulls / total) if total else 1.0
    return dg.AssetCheckResult(
        passed=bool(total) and ratio <= MAX_NULL_EMAIL_RATIO,
        severity=dg.AssetCheckSeverity.ERROR,
        metadata={"rows": total, "null_emails": nulls, "null_ratio": round(ratio, 4)},
    )


@dg.asset_check(
    asset=raw_orders,
    description=f"Détection d'anomalie de volume : |z-score| ≤ {ANOMALY_Z_THRESHOLD} sur {ANOMALY_HISTORY_DAYS} jours.",
)
def orders_volume_anomaly(duckdb: DuckDBResource) -> dg.AssetCheckResult:
    with duckdb.get_connection() as conn:
        rows = conn.execute(
            """
            select _partition_date, count(*) as n
            from raw.orders
            group by 1
            order by 1 desc
            limit ?
            """,
            [ANOMALY_HISTORY_DAYS + 1],
        ).fetchall()
    if not rows:
        return dg.AssetCheckResult(passed=False, severity=dg.AssetCheckSeverity.WARN, metadata={"reason": "no data"})
    latest_day, latest = rows[0]
    history = [float(n) for _, n in rows[1:]]
    score = zscore(float(latest), history)
    return dg.AssetCheckResult(
        passed=abs(score) <= ANOMALY_Z_THRESHOLD,
        severity=dg.AssetCheckSeverity.WARN,
        metadata={
            "partition": str(latest_day),
            "volume": int(latest),
            "history_mean": round(statistics.fmean(history), 2) if history else None,
            "zscore": round(score, 3),
        },
    )


ASSET_CHECKS = [customers_email_completeness, orders_volume_anomaly]
