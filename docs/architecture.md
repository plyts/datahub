# Architecture

## Vue d'ensemble

Une seule VM VirtualBox sous **SUSE Linux Enterprise Server 15**, Docker Engine, un projet Docker Compose (`datahub`), un réseau interne et des volumes persistants. **Seul Caddy publie des ports** (80/443) ; tous les autres services ne sont joignables que sur le réseau Docker.

| Conteneur | Image | Rôle | Mémoire max |
|---|---|---|---|
| `caddy` | caddy 2.11 | Point d'entrée unique, TLS optionnel, en-têtes de sécurité | 256 Mo |
| `oauth2-proxy` | oauth2-proxy 7.15 | Authentification OIDC devant l'UI Dagster et la doc dbt | 128 Mo |
| `keycloak` | Keycloak 26.8 | IAM / SSO (realm `datahub` importé au démarrage) | 1 Go |
| `datahub-ui` | build `services/datahub-ui` | Streamlit — Data Hub (11 modules) | 768 Mo |
| `datahub-exporter` | même image | Métriques Prometheus de Dagster | 256 Mo |
| `dagster-code` | build `services/dagster` | Code server gRPC ; **exécute les runs** (dbt Core + DuckDB) | 3 Go |
| `dagster-webserver` | même image | UI + API GraphQL (`/dagster`) | 1 Go |
| `dagster-daemon` | même image | Schedules, sensors, file d'attente, backfills, fraîcheur | 768 Mo |
| `postgres` | PostgreSQL 17 | Bases `dagster`, `keycloak`, `datahub`, `grafana` | 1 Go |
| `prometheus` | Prometheus 3.16 | Métriques, règles d'alerte, réception remote-write | 1 Go |
| `loki` | Loki 3.7 | Logs (rétention 30 jours) | 1 Go |
| `alloy` | Grafana Alloy 1.22 | Collecte des logs Docker, récepteur OTLP | 512 Mo |
| `grafana` | Grafana OSS 13.3 | Dashboards provisionnés, SSO Keycloak | 512 Mo |
| `node-exporter` | 1.12 | CPU / RAM / disque de la VM | 128 Mo |
| `cadvisor` | 0.61 | Ressources par conteneur | 256 Mo |
| `postgres-exporter` | 0.21 | Santé et activité PostgreSQL | 128 Mo |

Total des limites : environ 12 Go, compatible avec une VM de 12 à 16 Go (test `test_total_memory_fits_the_vm`).

## Routage (Caddy)

| Chemin | Service | Authentification |
|---|---|---|
| `/` | Streamlit Data Hub | OIDC applicatif (`st.login`) |
| `/auth/login`, `/auth/logout` | Streamlit | Points d'entrée OIDC de Streamlit (routés **avant** `/auth*`) |
| `/auth/*` | Keycloak | — |
| `/oauth2/*` | oauth2-proxy | — |
| `/dagster/*` | Dagster webserver | `forward_auth` oauth2-proxy, rôles ADMIN / DATA_ENGINEER |
| `/dbt-docs/*` | Site dbt docs (volume `dbt_artifacts`) | idem |
| `/grafana/*` | Grafana | OIDC Grafana (Keycloak) |

Le nom public (`DATAHUB_HOST`) est déclaré comme **alias réseau de Caddy** : à l'intérieur du réseau Docker, `http://datahub.local/...` pointe aussi vers Caddy. Les URL OIDC (issuer, découverte, endpoints) sont donc identiques pour les navigateurs et pour les conteneurs : pas de décalage d'issuer.

## Orchestration et transformation

```
raw/customers ─────────────► staging/stg_customers ─────────────┐
raw/orders    [partition] ─► staging/stg_orders ─┐               ├─► marts/dim_customers
raw/payments  [partition] ─► staging/stg_payments ─► intermediate/int_order_payments
                                                  └─► marts/fct_orders [partition, incrémental] ─► marts/agg_daily_revenue
reference/country_codes (seed dbt) ─────────────────────────────────────────────────────────────► marts/dim_customers
```

- **Clés d'assets = emplacement DuckDB** : `[schéma, table]` (`marts/fct_orders` ↔ `marts.fct_orders`).
- **dagster-dbt** découpe le projet dbt en trois multi-assets subsettables (amont, partitionné, aval), afin que le modèle incrémental partitionné se place entre des couches non partitionnées. Les tests dbt deviennent des **asset checks**.
- **Partitions** journalières (fuseau Europe/Paris). `BackfillPolicy.single_run()` : un backfill d'une plage s'exécute en un seul run. Le modèle `fct_orders` reçoit la fenêtre `[min_date, max_date)` en variables dbt.
- **Idempotence** : l'ingestion supprime puis réinsère ses partitions ; `fct_orders` est en `delete+insert`.
- **DuckDB = un seul écrivain** : toutes les opérations d'écriture sont dans le pool Dagster `duckdb` (limite 1), et les runs utilisent l'exécuteur in-process. Les statistiques des tables (nombre de lignes, schéma) sont lues **après** la fin du processus dbt, en lecture seule. Les fonctions `fetch_row_counts()` de dagster-dbt bloqueraient le fichier.
- **Runs** lancés dans le conteneur `dagster-code` par le `DefaultRunLauncher`, sans socket Docker.
- **Pipelines sans code** : une définition saisie dans le module 01 (sélection Dagster ou sélecteur dbt + cron) est stockée dans PostgreSQL, lue en lecture seule par la code location et compilée en job et schedule. Une définition invalide est ignorée et signalée, sans jamais casser la code location.

### Automatisations

| Type | Nom | Effet |
|---|---|---|
| Schedule | `daily_partition_schedule` | 05:30, partition J-1 : référentiels + ingestion + dbt + tests |
| Schedule | `weekly_marts_rebuild_schedule` | Dimanche 03:00, reconstruction des couches non partitionnées (arrêté par défaut) |
| Sensor | `landing_file_sensor` | Chaque CSV déposé dans le volume `landing` → `raw.landing_<nom>` |
| Sensor | `refresh_docs_after_daily_success` | Après chaque succès du pipeline quotidien → `dbt docs generate` + publication du catalogue |
| Sensor | `failure_notification_sensor` | Échec de run → webhook (Mattermost / Slack / Rocket.Chat) et/ou e-mail |
| Fraîcheur | `FreshnessPolicy` | Données brutes J-1 avant 09:00 ; marts de moins de 26 h |
| Retries | `RetryPolicy` + `run_retries` | Ingestion : 3 tentatives avec backoff exponentiel ; dbt : 1 ; runs tués par l'infra : 1 |

## Observabilité

- **Métriques** : l'exporter (`datahub_ui.exporter`) interroge l'API GraphQL de Dagster toutes les 30 s et expose les runs, durées, succès, files, fraîcheur, checks et daemons. Prometheus collecte aussi la VM, les conteneurs, PostgreSQL, Keycloak, Caddy, oauth2-proxy, Loki et Alloy.
- **Logs** : Alloy découvre les conteneurs via le socket Docker (lecture seule), ajoute les labels `container`, `service`, `level`, et pousse vers Loki.
- **Traces** : l'interface et l'exporter sont instrumentés avec OpenTelemetry (chaque appel HTTP vers Dagster, Keycloak, Loki ou Prometheus est un span) et envoient en OTLP à Alloy. Celui-ci calcule des **span metrics** (débit, erreurs, latence) et les écrit dans Prometheus. Aucun backend de traces supplémentaire n'est à administrer (Tempo peut être ajouté plus tard si besoin).
- **Alertes** : `services/prometheus/rules/datahub.yml`, testées avec `promtool test rules`. Elles sont visibles dans Grafana et dans le module 10.

## Choix et limites assumés

- Pas de React, Next.js ni FastAPI : Streamlit appelle directement les API (Dagster GraphQL, Keycloak Admin, Loki, Prometheus) et PostgreSQL. Comme Streamlit s'exécute côté serveur, les contrôles RBAC sont faits côté serveur.
- Pas de second orchestrateur, moteur de logs ou catalogue : le Data Hub **compose** Dagster, dbt, Loki et Prometheus.
- DuckDB convient à une équipe de 3 personnes et à des volumes de quelques dizaines de Go. Au-delà, la même structure dbt peut cibler PostgreSQL ou un autre adaptateur.
- L'aperçu de données du catalogue est indisponible pendant qu'un pipeline écrit dans DuckDB (verrou d'écriture). Un message explicite est affiché.

## Licences

| Composant | Licence |
|---|---|
| Dagster OSS, dagster-dbt, dagster-duckdb | Apache 2.0 |
| dbt Core, dbt-duckdb | Apache 2.0 |
| DuckDB | MIT |
| Streamlit | Apache 2.0 |
| Keycloak | Apache 2.0 |
| PostgreSQL | PostgreSQL License |
| Prometheus, node-exporter, postgres-exporter | Apache 2.0 |
| Grafana OSS, Loki, Alloy | AGPL-3.0 (utilisés tels quels, non modifiés) |
| OpenTelemetry | Apache 2.0 |
| Caddy, oauth2-proxy, cAdvisor | Apache 2.0 / MIT |
| Gitea | MIT ; Woodpecker CI : Apache 2.0 |

Les licences exactes des versions retenues doivent être revérifiées avant la mise en production.
