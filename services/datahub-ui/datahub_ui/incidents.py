"""Diagnosis and classification of pipeline failures (module 06)."""

from __future__ import annotations

import re
from dataclasses import dataclass

# (category, severity, regex) — evaluated in order, first match wins.
RULES: list[tuple[str, str, re.Pattern]] = [
    (
        "Ressources (mémoire / disque)",
        "critical",
        re.compile(r"out of memory|oom|killed|no space left|disk quota|cannot allocate", re.I),
    ),
    (
        "Verrou DuckDB / concurrence",
        "medium",
        re.compile(r"could not set lock|conflicting lock|database is locked", re.I),
    ),
    (
        "Connexion / réseau",
        "high",
        re.compile(
            r"connection refused|could not connect|timed? ?out|name or service not known|unreachable|"
            r"connection reset|ssl",
            re.I,
        ),
    ),
    (
        "Qualité des données (test dbt / check)",
        "high",
        re.compile(
            r"blocking (asset )?check|check .* failed|failure in test|data_tests?|got \d+ results?, "
            r"configured to fail|asset check",
            re.I,
        ),
    ),
    (
        "Erreur SQL / dbt",
        "high",
        re.compile(
            r"(catalog|binder|parser|conversion|constraint) error|syntax error|dbt.*(compilation|runtime) error|"
            r"compilation error|database error|DbtCliRuntimeError",
            re.I,
        ),
    ),
    (
        "Configuration",
        "medium",
        re.compile(
            r"config(uration)? (error|invalid)|missing required|environment variable|DagsterInvalidConfig", re.I
        ),
    ),
    (
        "Erreur Python",
        "medium",
        re.compile(r"Traceback|Error:|Exception|KeyError|ValueError|TypeError|AttributeError", re.I),
    ),
]


@dataclass(frozen=True)
class Diagnosis:
    category: str
    severity: str
    hint: str


HINTS = {
    "Ressources (mémoire / disque)": "Vérifier la RAM / le disque de la VM (module Performance) et le volume des données.",
    "Verrou DuckDB / concurrence": "Un autre traitement écrivait dans DuckDB. Relancer : le pool `duckdb` sérialise les écritures.",
    "Connexion / réseau": "Vérifier que la source / le service est joignable (Administration > Santé des services).",
    "Qualité des données (test dbt / check)": "Ouvrir le Data Quality Center pour voir le test en échec et les lignes fautives.",
    "Erreur SQL / dbt": "Lire le SQL compilé du modèle (Data Catalog) et l'erreur dbt exacte ci-dessous.",
    "Configuration": "Vérifier la configuration du run et les variables d'environnement du service.",
    "Erreur Python": "Lire la stack trace : l'erreur vient du code d'un asset Python.",
    "Inconnu": "Consulter les logs complets du run.",
}


def classify(message: str) -> Diagnosis:
    for category, severity, pattern in RULES:
        if pattern.search(message or ""):
            return Diagnosis(category, severity, HINTS[category])
    return Diagnosis("Inconnu", "low", HINTS["Inconnu"])
