# Sécurité

## Authentification (SSO)

- **Keycloak** est l'unique fournisseur d'identité (realm `datahub`, importé depuis `services/keycloak/realm/datahub-realm.json`, sans aucun secret en clair : tous sont substitués depuis l'environnement).
- **Data Hub** : OIDC *authorization code* + PKCE via `st.login()` (client confidentiel `datahub-ui`). Les rôles arrivent dans le claim `roles` du jeton d'identité.
- **UI Dagster et doc dbt** : protégées par **oauth2-proxy** (client `dagster-proxy`) en `forward_auth` dans Caddy. Seuls `ADMIN` et `DATA_ENGINEER` y accèdent ; les autres reçoivent une 403 (testé en E2E). L'API GraphQL n'est jamais exposée sans authentification.
- **Grafana** : OIDC (client `grafana`) ; rôle Grafana dérivé du rôle Data Hub (`ADMIN`→Admin, `DATA_ENGINEER`→Editor, `ANALYST`/`VIEWER`→Viewer, aucun rôle → accès refusé).
- Politique du realm : 12 caractères minimum (majuscule, minuscule, chiffre), historique de 3 mots de passe, **protection contre la force brute**, pas d'auto-inscription, journalisation des événements de connexion et d'administration.
- MFA : activable dans Keycloak (*Authentication → Required actions → Configure OTP*), sans modification du Data Hub.

## Autorisations (RBAC)

| Permission | ADMIN | DATA_ENGINEER | ANALYST | VIEWER |
|---|:-:|:-:|:-:|:-:|
| Dashboard, historiques, performance | ✅ | ✅ | ✅* | ✅ |
| Lancer / relancer / arrêter des runs, backfills | ✅ | ✅ | | |
| Créer des pipelines, modifier les schedules | ✅ | ✅ | | |
| Catalogue, lineage, qualité, aperçu des données | ✅ | ✅ | ✅ | |
| Glossaire (écriture), incidents | ✅ | ✅ | | |
| Logs, environnements | ✅ | ✅ | | |
| Administration, utilisateurs, connexions, audit | ✅ | | | |

\* sans la page Performance. La matrice complète est dans `services/datahub-ui/datahub_ui/rbac.py` et affichée dans le module 11.

- Les pages sont filtrées par rôle, et **chaque action sensible est revérifiée côté serveur** (`user.require(...)`) juste avant l'appel à Dagster, Keycloak ou PostgreSQL.
- Les colonnes marquées `pii` dans dbt sont masquées dans l'aperçu de données pour les rôles autres qu'ADMIN et DATA_ENGINEER.
- **Journal d'audit** : chaque action (lancement, relance, backfill, schedule, pipeline, glossaire, incident, utilisateurs, aperçu de données, accès refusé) est écrite dans `datahub.audit_log` et dans les logs (Loki). Elle est consultable et exportable (CSV) dans le module 10, et visible dans Grafana.
- Le mode `DATAHUB_AUTH_MODE=none` (développement local) est **refusé au démarrage** hors de l'environnement dev.

## Moindre privilège

| Compte | Accès |
|---|---|
| PostgreSQL `dagster`, `keycloak`, `grafana` | Propriétaire de **sa** base uniquement (`REVOKE ALL ... FROM PUBLIC`) |
| `datahub_app` | Base `datahub` (UI : migrations, écriture) |
| `datahub_ro` | `SELECT` sur `pipeline_definitions`, `audit_log`, `incidents` (code location Dagster, Grafana). Aucun accès à `data_connections` (testé) |
| `postgres_exporter` | Rôle `pg_monitor`, sans accès aux données (testé) |
| Keycloak `datahub-admin-api` | Compte de service : `view/manage/query-users`, `query-groups`, `view-clients`, `view-realm` ; jamais `realm-admin` |
| Conteneurs applicatifs | Utilisateur non-root (uid 1000), données montées en lecture seule dans l'UI |
| Alloy | Socket Docker en lecture seule |

## Secrets

- Tous les secrets sont dans `.env` (droits `600`), générés par `scripts/generate-secrets.sh`, **jamais commités** (`.gitignore` + test `test_no_env_file_is_tracked`).
- Le Data Hub ne stocke **jamais** une valeur de secret : les connexions ne référencent qu'un **nom** de variable (contrainte SQL et validation applicative).
- Rotation : `scripts/generate-secrets.sh --rotate NOM` puis `make deploy`. Pour les secrets OIDC, mettez aussi à jour le client dans la console Keycloak (le realm n'est importé qu'à la création).
- `secrets.toml` de Streamlit est généré au démarrage du conteneur (droits `600`), à partir de l'environnement.

## Réseau et transport

- Un seul point d'exposition (Caddy). PostgreSQL, Dagster, Prometheus, Loki, Keycloak (port de management), etc. ne sont pas joignables depuis l'extérieur.
- En-têtes : `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, sans en-tête `Server` (testés en E2E).
- HTTPS : voir [deployment-sles.md](deployment-sles.md#4-http-ou-https). En prod, `COOKIE_SECURE=true`.
- Pare-feu de la VM : seuls SSH, 80 et 443 sont ouverts (`bootstrap-sles.sh`).
- SELinux reste en mode enforcing sur SLES 16 / Leap 16 : seul `/opt/datahub` est étiqueté pour les conteneurs ; Alloy et node-exporter (socket Docker, système de fichiers de l'hôte) tournent sans confinement SELinux, cAdvisor est privilégié.

## Points d'attention

- Le realm n'est importé qu'au **premier** démarrage de Keycloak. Les modifications ultérieures se font dans la console, ou via un export/import contrôlé.
- Les comptes de démonstration sont refusés en prod, mais supprimez-les aussi de STAGING avant toute ouverture à des utilisateurs réels.
- Le socket Docker monté dans Alloy et cAdvisor donne une visibilité sur l'hôte : ces conteneurs sont des images officielles épinglées.
