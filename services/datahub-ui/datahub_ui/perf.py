"""Performance analytics on Dagster runs (module 08)."""

from __future__ import annotations

import pandas as pd

FINISHED = ("SUCCESS", "FAILURE", "CANCELED")


def runs_frame(runs: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(runs or [], columns=["runId", "jobName", "status", "startTime", "endTime", "duration", "tags"])
    if df.empty:
        return df.assign(started=pd.Series(dtype="datetime64[ns, UTC]"), partition=pd.Series(dtype=str))
    df["started"] = pd.to_datetime(df["startTime"], unit="s", utc=True)
    df["partition"] = df["tags"].apply(lambda t: (t or {}).get("dagster/partition", ""))
    return df


def duration_stats(df: pd.DataFrame) -> pd.DataFrame:
    done = df[df["status"].isin(FINISHED) & df["duration"].notna()]
    if done.empty:
        return pd.DataFrame(columns=["jobName", "runs", "p50_s", "p90_s", "max_s", "success_rate"])
    grouped = done.groupby("jobName")
    return pd.DataFrame(
        {
            "runs": grouped.size(),
            "p50_s": grouped["duration"].median().round(1),
            "p90_s": grouped["duration"].quantile(0.9).round(1),
            "max_s": grouped["duration"].max().round(1),
            "success_rate": grouped["status"].apply(lambda s: round((s == "SUCCESS").mean() * 100, 1)),
        }
    ).reset_index()


def detect_slowdowns(df: pd.DataFrame, factor: float = 1.5, min_history: int = 5) -> pd.DataFrame:
    """Successful runs whose duration exceeds `factor` x the median of the previous runs of the same job."""
    done = df[(df["status"] == "SUCCESS") & df["duration"].notna()].sort_values("startTime")
    flagged = []
    for job, group in done.groupby("jobName"):
        durations = group["duration"].tolist()
        for i in range(min_history, len(durations)):
            baseline = pd.Series(durations[:i]).median()
            if baseline > 0 and durations[i] > factor * baseline:
                row = group.iloc[i]
                flagged.append(
                    {
                        "jobName": job,
                        "runId": row["runId"],
                        "started": row["started"],
                        "duration_s": round(durations[i], 1),
                        "baseline_s": round(baseline, 1),
                        "ratio": round(durations[i] / baseline, 2),
                    }
                )
    return pd.DataFrame(flagged, columns=["jobName", "runId", "started", "duration_s", "baseline_s", "ratio"])


def compare_steps(step_stats_a: list[dict], step_stats_b: list[dict]) -> pd.DataFrame:
    """Step-by-step duration comparison of two runs."""

    def frame(stats):
        return {
            s["stepKey"]: (s["endTime"] - s["startTime"])
            if s.get("endTime") is not None and s.get("startTime") is not None
            else None
            for s in stats
        }

    a, b = frame(step_stats_a), frame(step_stats_b)
    rows = []
    for step in sorted(set(a) | set(b)):
        da, db = a.get(step), b.get(step)
        delta = round(db - da, 2) if da is not None and db is not None else None
        rows.append(
            {
                "step": step,
                "run_a_s": None if da is None else round(da, 2),
                "run_b_s": None if db is None else round(db, 2),
                "delta_s": delta,
            }
        )
    return pd.DataFrame(rows, columns=["step", "run_a_s", "run_b_s", "delta_s"])
