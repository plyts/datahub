# CI/CD

## GitHub Actions (`.github/workflows/ci.yml`)

À chaque push, pull request et tag :

| Job | Contenu |
|---|---|
| Lint | ruff (lint + format) sur les deux services, shellcheck sur tous les scripts |
| Dagster + dbt | 44 tests, dont de vrais runs du pipeline sur DuckDB (dbt build, tests, unit tests dbt) |
| UI | 86 tests (chaque page × chaque rôle, RBAC, clients, lineage, traces) |
| Configuration | Tests statiques + `docker compose config` (dev, staging, prod, ci) + `promtool test rules` + `loki -verify-config` + `alloy fmt` + `caddy validate`, avec les images officielles |
| Intégration | PostgreSQL 17 + Dagster réel avec le `dagster.yaml` de production : runs, ré-exécution, backfill, schedules, sensors, pipeline créé depuis l'UI, migrations, moindre privilège |
| E2E | Build des images, **stack Docker Compose complète**, healthchecks, comptes de démo, smoke tests, SSO pour chaque rôle, RBAC de l'UI Dagster, pipeline lancé via la plateforme, cibles Prometheus toutes UP, logs dans Loki, dashboards Grafana provisionnés. Logs des conteneurs en artefact |

## Forge on-premise : Gitea + Woodpecker (`--profile cicd`)

```bash
docker compose --env-file envs/prod.env --env-file .env -f docker-compose.yml -f compose/docker-compose.cicd.yml --profile cicd up -d
```

1. Gitea : `http://<hôte>:3001`. Créez le compte administrateur, puis l'organisation et le dépôt (miroir de GitHub, ou dépôt principal).
2. Dans Gitea : *Paramètres → Applications → OAuth2* : créez une application avec la redirection `http://<hôte>:8000/authorize`.
3. Ajoutez dans `.env` : `WOODPECKER_GITEA_CLIENT`, `WOODPECKER_GITEA_SECRET` et `WOODPECKER_AGENT_SECRET` (`openssl rand -hex 32`), puis relancez le profil.
4. Woodpecker : `http://<hôte>:8000`, activez le dépôt.

Pipelines :

- `.woodpecker/ci.yml` : mêmes portes qualité que GitHub Actions (lint, tests, configuration, règles d'alerte).
- `.woodpecker/deploy.yml` : déploiement **sur la VM elle-même** par un agent Woodpecker en backend *local* (label `datahub-host`), qui exécute `make deploy` dans `/opt/datahub`. Un tag `v*` déploie en STAGING, la promotion manuelle (événement *deployment*, cible `prod`) déploie en PROD, et `ROLLBACK_TAG=vX.Y.Z` lance un rollback.

## Gestion de versions

- `main` est toujours déployable (CI verte obligatoire, branche protégée recommandée).
- Branches `feature/*` → merge request → revue + CI.
- Release : tag SemVer `vX.Y.Z` ; la version est reprise dans `envs/*.env` (`DATAHUB_VERSION`) et dans le tag des images.
- Les dépendances Python sont verrouillées (`requirements.lock`, généré par `uv pip compile requirements.in`). Pour les mettre à jour : modifiez `requirements.in`, régénérez le lock, puis laissez la CI valider.
