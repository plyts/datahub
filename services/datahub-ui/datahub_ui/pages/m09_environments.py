"""09. Environment Management — DEV / STAGING / PROD, Git versioning, configuration, CI/CD and rollback."""

import pandas as pd
import streamlit as st

from datahub_ui.ui import badge, fmt_ts, header, safe

ENVIRONMENTS = [
    {
        "Environnement": "DEV",
        "Usage": "Développement, données synthétiques",
        "Branche Git": "feature/* → develop",
        "Déploiement": "make up ENV=dev (poste / VM de dev)",
        "Auth": "Keycloak (ou none en local)",
    },
    {
        "Environnement": "STAGING",
        "Usage": "Recette, tests de non-régression",
        "Branche Git": "develop / tag rc-*",
        "Déploiement": "CI : make deploy ENV=staging",
        "Auth": "Keycloak",
    },
    {
        "Environnement": "PROD",
        "Usage": "Production",
        "Branche Git": "main / tag v*",
        "Déploiement": "CI après validation : make deploy ENV=prod",
        "Auth": "Keycloak (obligatoire)",
    },
]

SAFE_SETTINGS = (
    "env",
    "version",
    "git_sha",
    "public_url",
    "auth_mode",
    "dagster_graphql_url",
    "dagster_location",
    "loki_url",
    "prometheus_url",
    "keycloak_url",
    "keycloak_realm",
    "dbt_artifacts_dir",
    "duckdb_path",
    "timezone",
)


def render(ctx) -> None:
    s = ctx.settings
    header("09", "Environment Management", "Environnements, versionnement Git, configuration, CI/CD et rollback")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Environnement", s.env.upper())
    c2.metric("Version", s.version)
    c3.metric("Commit", s.git_sha[:8])
    locations = safe(ctx.dagster.locations, [], "Code locations")
    c4.metric("Code location", locations[0]["status"] if locations else "—")

    tabs = st.tabs(["🌍 Environnements", "⚙️ Configuration", "✅ Validation avant déploiement", "🚚 CI/CD & rollback"])
    with tabs[0]:
        st.dataframe(pd.DataFrame(ENVIRONMENTS), hide_index=True, use_container_width=True)
        st.markdown(
            "Chaque environnement = un fichier `envs/<env>.env` + un fichier DuckDB dédié + "
            "un realm Keycloak/URL publique propres. Les images Docker sont identiques (tag = commit Git)."
        )
    with tabs[1]:
        st.dataframe(
            pd.DataFrame([{"Paramètre": k, "Valeur": str(getattr(s, k))} for k in SAFE_SETTINGS]),
            hide_index=True,
            use_container_width=True,
        )
        st.caption("Les secrets (mots de passe, client secrets) ne sont jamais affichés.")
        for loc in locations:
            st.write(
                f"Code location **{loc['name']}** : {badge('SUCCESS' if loc['status'] == 'LOADED' else 'FAILURE')} "
                f"{loc['status']} — mise à jour {fmt_ts(loc['updated'])}"
            )
            if loc["error"]:
                st.error(loc["error"])
    with tabs[2]:
        results = ctx.artifacts().test_results()
        failed = [r for r in results if r["status"] not in ("pass", "success")]
        st.markdown(
            "Chaque merge request exécute en CI : **lint** (ruff), **tests unitaires** (pytest), "
            "**dbt build** sur DuckDB (modèles + tests + unit tests), **validation des configurations** "
            "(Prometheus, Loki, Alloy, Caddy, Keycloak, Compose) et un **test E2E** de la stack Docker Compose complète."
        )
        st.metric(
            "Derniers tests dbt publiés",
            len(results),
            delta=f"{len(failed)} en échec" if failed else "tous OK",
            delta_color="inverse" if failed else "normal",
        )
        if failed:
            st.dataframe(pd.DataFrame(failed), hide_index=True)
    with tabs[3]:
        st.markdown(
            "**Déploiement** (sur la VM SLES) :\n"
            "```bash\n"
            "git fetch --tags && git checkout v1.2.0\n"
            "make deploy ENV=prod            # build + migrations + up -d + smoke tests\n"
            "```\n"
            "**Rollback** vers la version précédente :\n"
            "```bash\n"
            "make rollback ENV=prod TAG=v1.1.0   # images taguées par version, données conservées\n"
            "```\n"
            "Les sauvegardes PostgreSQL + DuckDB sont faites avant chaque déploiement (`make backup`)."
        )
