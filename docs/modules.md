# Guide des modules

Le Data Hub s'ouvre sur un **dashboard** : runs des dernières 24 h, échecs, file d'attente, prochaines exécutions, fraîcheur, santé des services. Le menu n'affiche que les modules autorisés pour votre rôle.

## 01 · Pipeline Management
- **Pipelines** : liste des jobs Dagster. Lancement manuel, avec choix de la partition (jour) et configuration JSON optionnelle.
- **Assets & modèles** : tous les assets (ingestion Python, modèles dbt découverts automatiquement), avec groupe, type, partitionnement, checks, fraîcheur, dernière matérialisation et propriétaires.
- **Dépendances** : graphe complet, coloré par couche.
- **Historique** : runs filtrables, détail des étapes, relance depuis l'échec, arrêt d'un run.
- **Pipelines personnalisés** : création sans code. Exemples de sélection :
  - Dagster : `group:marts_finance`, `key:"raw/orders"+` (l'asset et tout l'aval), `tag:layer=raw`, `kind:dbt and group:staging` ;
  - dbt : `tag:finance`, `+agg_daily_revenue` (le modèle et son amont), `path:models/marts`.
  - Un cron optionnel crée un schedule. Après l'enregistrement, Dagster est rechargé et la colonne « Chargé dans Dagster » confirme la prise en compte.

## 02 · Scheduling Center
Schedules (cron, fuseau, prochaine exécution, démarrage/arrêt), sensors (type, intervalle, dernier tick), frise des prochaines exécutions, couverture des partitions (matérialisées, en échec, manquantes) et aide cron.

## 03 · Data Lineage Explorer
Graphe amont/aval autour d'un asset (profondeur réglable), **analyse d'impact** (assets touchés par couche, exposures dbt concernées), **lineage de colonnes** pour les modèles dbt (le SQL compilé est analysé avec sqlglot) et historique des matérialisations (date, partition, lignes, run).

## 04 · Data Catalog
Recherche par nom, description, colonne ou tag. Fiche détaillée : description, owner, tags, termes du glossaire, colonnes (type, description, PII, tests), SQL source et compilé, aperçu de 100 lignes (PII masquées selon le rôle, action auditée). Onglet **Glossaire** : termes, définitions, synonymes, assets liés (édition pour ADMIN et DATA_ENGINEER). Le lien *Documentation dbt* ouvre le site dbt complet.

## 05 · Data Quality Center
Tous les contrôles (tests dbt `unique`, `not_null`, `relationships`, `accepted_values`, `not_negative`, `row_count_min`, tests singuliers et checks Python), leur statut, sévérité et caractère bloquant ; politiques de fraîcheur ; **détection d'anomalies de volume** (bande ± 3σ) ; historique de chaque contrôle ; derniers résultats dbt.

## 06 · Logs & Error Management
- **Erreurs récentes** : runs en échec, avec erreur, **catégorie** (SQL/dbt, Python, qualité, connexion, verrou DuckDB, ressources, configuration) et piste de résolution. Chaque échec crée un incident.
- **Logs d'un run** : événements filtrés par niveau ou texte, stack traces.
- **Logs des conteneurs** : requêtes Loki par conteneur, niveau ou texte, sur une période donnée.
- **Incidents** : suivi (ouvert, pris en charge, résolu), assignation et cause racine.

## 07 · Backfill & Recovery
Backfill d'une plage de dates ou des seules partitions manquantes/en échec, avec ou sans les modèles en aval, et confirmation explicite. Suivi et annulation des backfills, relance des runs en échec (depuis l'étape fautive ou complète).

## 08 · Performance Monitoring
Statistiques de durée par pipeline (p50, p90, max, taux de succès), nuage des durées, **détection des ralentissements** (durée > N × médiane historique), comparaison de deux runs étape par étape, volumes traités (lignes par matérialisation), CPU / RAM / disque de la VM et top des conteneurs.

## 09 · Environment Management
Environnement courant, version, commit, état de la code location, configuration non secrète, matrice DEV/STAGING/PROD, validations avant déploiement, procédure de déploiement et de rollback.

## 10 · Administration & Security
Santé de chaque service (latence), cibles Prometheus et alertes actives ; utilisateurs Keycloak (rôles effectifs, activation, rôles directs, fermeture des sessions) ; connexions aux sources (avec seulement des **références** de secrets) ; politique des secrets ; **journal d'audit** filtrable et exportable ; état des daemons Dagster et rechargement de la code location.

## 11 · Identity & Access Management
Profil, groupes, rôles, permissions effectives, claims du jeton OIDC (date de connexion, expiration), lien vers le compte Keycloak (mot de passe, MFA, sessions), matrice RBAC complète ; pour les administrateurs : utilisateurs, sessions actives, groupes et accès à la console du realm.
