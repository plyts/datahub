{% docs __overview__ %}
# Data Hub — projet dbt

Projet de transformation SQL de la plateforme **Data Hub** (dbt Core + DuckDB, orchestré par Dagster OSS).

| Couche | Schéma | Matérialisation | Rôle |
|---|---|---|---|
| Sources | `raw` | tables (Dagster) | Données brutes ingérées et partitionnées par jour |
| Référentiel | `reference` | seeds | Données de référence versionnées dans Git |
| Staging | `staging` | vues | Nettoyage, typage, normalisation |
| Intermediate | `intermediate` | vues | Agrégations techniques réutilisables |
| Marts | `marts` | tables / incrémental | Modèles métier exposés aux utilisateurs |

Les tests dbt sont remontés comme **asset checks** dans Dagster et visibles dans le *Data Quality Center*.
{% enddocs %}
