"""Test scenarios of the simulated source (fault injection), launched from module 01.

Keys must match `FAULTS` in services/dagster/datahub_pipelines/generators.py
(checked by tests/config/test_scenarios_sync.py). Never offered in production.
"""

FAULTS = {
    "none": "Données normales",
    "volume_spike": "Pic de volume : 5 fois plus de commandes (anomalie détectée, avertissement non bloquant)",
    "negative_amounts": "Montants négatifs sur 5 % des commandes (tests dbt en échec, run en échec)",
    "null_emails": "10 % d'emails clients vides (contrôle bloquant en échec, aval non exécuté)",
    "source_flaky": "Source instable : 1re tentative en erreur 503, reprise automatique réussie",
    "source_unavailable": "Source indisponible : erreur 503 à chaque tentative (échec après 3 reprises)",
}
CUSTOMER_FAULTS = {"null_emails"}


def fault_run_config(fault: str) -> dict:
    """Dagster run config injecting `fault` in the right ingestion asset ({} for normal data)."""
    if fault not in FAULTS:
        raise ValueError(f"unknown fault {fault!r}")
    if fault == "none":
        return {}
    op = "raw__customers" if fault in CUSTOMER_FAULTS else "raw__orders"
    return {"ops": {op: {"config": {"fault": fault}}}}


def merge_run_config(base: dict, extra: dict) -> dict:
    merged = {**base}
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_run_config(merged[key], value)
        else:
            merged[key] = value
    return merged
