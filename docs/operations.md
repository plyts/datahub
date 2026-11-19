# Exploitation

## Environnements

| | DEV | STAGING | PROD |
|---|---|---|---|
| Fichier | `envs/dev.env` | `envs/staging.env` | `envs/prod.env` |
| Override Compose | `compose/docker-compose.dev.yml` (ports de debug sur 127.0.0.1) | `…staging.yml` | `…prod.yml` (`restart: always`, plus de mémoire pour dagster-code) |
| URL | `http://datahub.local` | `http://datahub-staging.local` | `https://datahub.internal` |
| Base DuckDB | `datahub_dev.duckdb` | `datahub_staging.duckdb` | `datahub_prod.duckdb` |
| Comptes de démo | oui | oui | **refusés** |
| Mode sans authentification | possible (`DATAHUB_AUTH_MODE=none`) | refusé | refusé |

Chaque environnement tourne sur sa propre VM (recommandé), ou sur la même VM avec un `COMPOSE_PROJECT_NAME` et des ports distincts. Les images sont identiques : seule la configuration change.

## Commandes courantes

```bash
make help                       # toutes les commandes
make up ENV=prod                # démarrer / mettre à jour
make ps ENV=prod                # état
make logs ENV=prod S=dagster-code
make restart ENV=prod S=datahub-ui
make down ENV=prod              # arrêt (les volumes sont conservés)
```

## Déploiement et rollback

```bash
git fetch --tags && git checkout v1.2.0
make deploy ENV=prod            # config -> sauvegarde -> build -> up -> healthchecks -> smoke tests
```

`deploy` refuse de continuer si la configuration Compose est invalide, et sauvegarde les bases avant toute modification. Chaque déploiement est tracé dans `backups/deploy-history.log`.

Rollback :

```bash
make rollback ENV=prod TAG=v1.1.0
```

Le code et les images reviennent à la version taguée, et les données sont conservées. Si une migration de base doit aussi être annulée, restaurez la sauvegarde prise avant le déploiement fautif.

Le flux recommandé est le suivant : merge request → CI verte → tag `vX.Y.Z` → déploiement STAGING → recette → déploiement PROD (manuel). Voir [cicd.md](cicd.md).

## Sauvegardes

```bash
make backup ENV=prod            # backups/<horodatage>/ : pg_dump par base + export DuckDB (Parquet) + artefacts dbt + fichiers d'atterrissage
make restore ENV=prod BACKUP=backups/20261119-030000
```

À planifier chaque nuit (cron de la VM, 02:30, avant le pipeline de 05:30) :

```cron
30 2 * * * cd /opt/datahub && make backup ENV=prod >> /var/log/datahub-backup.log 2>&1
```

Rétention locale : 14 jours (`BACKUP_RETENTION_DAYS`). Copiez les sauvegardes hors de la VM, chiffrées (restic, borg ou `age`). Testez une restauration au moins une fois par trimestre.

## Supervision et alertes

| Où | Quoi |
|---|---|
| Data Hub — Dashboard | Runs 24 h, échecs, file d'attente, fraîcheur, santé des services |
| Grafana — *Pipelines & qualité* | Runs par statut, durées, checks en échec, fraîcheur, daemons |
| Grafana — *Infrastructure* | CPU / RAM / disque, conteneurs, PostgreSQL, trafic Caddy, latence des appels (traces) |
| Grafana — *Logs & audit* | Erreurs par conteneur, logs, journal d'audit |
| Prometheus | Règles `services/prometheus/rules/datahub.yml` |

| Alerte | Déclenchement |
|---|---|
| `DagsterApiDown` | API Dagster muette depuis 5 min |
| `DagsterDaemonUnhealthy` | Un daemon (scheduler, sensors, file…) en échec |
| `PipelineLastRunFailed` | Dernier run d'un pipeline en échec |
| `DailyPipelineLate` | Pas de run quotidien terminé depuis 26 h |
| `BlockingQualityCheckFailed` | Check qualité de sévérité ERROR en échec |
| `AssetNotFresh` | Asset en fraîcheur dégradée depuis 15 min |
| `TargetDown`, `PostgresDown` | Service injoignable |
| `HostHighCpu`, `HostHighMemory`, `HostDiskAlmostFull` | Ressources de la VM |

Notifications d'échec de pipeline : renseignez `NOTIFY_WEBHOOK_URL` (Mattermost, Slack ou Rocket.Chat) et/ou `SMTP_HOST` + `NOTIFY_EMAIL_TO` dans `.env`, puis `make up`. Pour router aussi les alertes Prometheus, configurez un *contact point* dans Grafana (Alerting).

## Dépôt de fichiers (sensor)

Les fichiers CSV déposés dans le volume `landing` sont chargés automatiquement dans `raw.landing_<nom>`, puis déplacés dans `_processed/` :

```bash
docker compose cp ventes_2026.csv dagster-code:/data/landing/
```

## Ajouter un pipeline

- **Sans code** : *01 · Pipeline Management → Pipelines personnalisés* (sélection d'assets + cron).
- **Nouveau modèle dbt** : ajoutez le SQL et sa documentation/ses tests YAML dans `services/dagster/dbt_project/models/...`, puis `make test-dagster`, merge request, déploiement. Le modèle apparaît automatiquement comme asset.
- **Nouvelle source** : écrivez un asset Python dans `services/dagster/datahub_pipelines/assets/` (exemple : `ingestion.py`), déclarez la table dans `models/staging/_sources.yml` et ajoutez-la au pool `duckdb`.

## Incidents fréquents

| Symptôme | Cause probable | Action |
|---|---|---|
| Aperçu du catalogue « base occupée » | Un pipeline écrit dans DuckDB | Réessayer après le run |
| Run en file qui ne démarre pas | Daemon arrêté, ou pool `duckdb` occupé | *10 · Administration → Administration* (daemons), *Performance* (runs en cours) |
| Échec « Could not set lock » | Écriture concurrente hors du pool | Relancer (*07*) ; vérifier que le nouvel asset utilise `pool=DUCKDB_POOL` |
| Boucle de connexion Keycloak | URL publique incohérente | `DATAHUB_PUBLIC_URL` = URL réellement tapée par les utilisateurs ; `make up` |
| `HTTPS required` à la connexion | Accès depuis une IP publique en HTTP | Passer en HTTPS (voir déploiement) |
| Pipeline personnalisé « rejeté » | Sélection invalide | Corriger la sélection dans le module 01 (détail dans les logs de `dagster-code`) |
