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

## Déploiement continu « pull » (recommandé avec un dépôt public)

Le dépôt étant public, **aucun runner GitHub n'est installé sur les machines** : GitHub déconseille les
runners auto-hébergés sur un dépôt public (une pull request pourrait exécuter du code sur la machine).
À la place, chaque VM/serveur exécute `scripts/deployer.py` toutes les 2 minutes (timer systemd) :

1. `git fetch` (HTTPS sortant uniquement) ;
2. cible = tête de `main` (dev/staging) ou plus haut tag `vX.Y.Z` (prod) ;
3. déploiement **seulement si le workflow `CI` est vert sur ce commit exact** ;
4. `scripts/deploy.sh <env>` (sauvegarde, build, démarrage, healthchecks, smoke tests) ;
5. échec → **retour automatique** au dernier commit déployé avec succès ; le commit fautif n'est pas retenté ;
6. chaque étape est publiée comme *deployment status* GitHub (token optionnel), dans `history.jsonl`,
   dans journald et sur le webhook de notification.

```bash
sudo GITHUB_TOKEN=github_pat_... ./scripts/install-deployer.sh staging   # ou prod
journalctl -u datahub-deployer@staging -f
make deployer-status ENV=staging
python3 scripts/deployer.py --env prod rollback --to v1.2.0               # retour arrière manuel
```

Les scénarios (nominal, attente de CI, CI rouge, déploiement cassé avec retour automatique, échec du
retour, verrou, GitHub injoignable, suivi des tags en prod) sont testés dans `tests/deployer`.

## Scénarios de test de la source simulée

Le module 01 (hors production) permet de lancer `daily_partition_job` avec une panne injectée :
`volume_spike`, `negative_amounts`, `null_emails`, `source_flaky`, `source_unavailable`. Les effets réels
(contrôle en échec, run en échec, reprise automatique, récupération par relance) sont testés dans
`services/dagster/tests/test_faults.py`.

La relance d'une partition la **remplace entièrement**, jusque dans le mart incrémental `fct_orders`
(un `pre_hook` vide la fenêtre avant insertion) : après un `volume_spike`, les 4 × commandes en trop
disparaissent aussi de `marts.fct_orders`, pas seulement de `raw.orders`.

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
