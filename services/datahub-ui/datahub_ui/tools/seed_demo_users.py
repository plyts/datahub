"""Create one demo user per Data Hub role (DEV / CI only, refused in production).

    docker compose exec datahub-ui python -m datahub_ui.tools.seed_demo_users

Passwords come from DEMO_USERS_PASSWORD (same password for the 4 demo accounts).
"""

from __future__ import annotations

import os
import sys

from datahub_ui.clients.keycloak import KeycloakAdmin
from datahub_ui.config import get_settings

DEMO_USERS = [
    ("alice.admin", "/admins", "Alice"),
    ("eric.engineer", "/data-engineers", "Éric"),
    ("anna.analyst", "/analysts", "Anna"),
    ("victor.viewer", "/viewers", "Victor"),
]


def main() -> int:
    settings = get_settings()
    if settings.env == "prod":
        print("Refusé : les comptes de démonstration sont interdits en production.", file=sys.stderr)
        return 2
    password = os.getenv("DEMO_USERS_PASSWORD")
    if not password:
        print("DEMO_USERS_PASSWORD is required", file=sys.stderr)
        return 1
    kc = KeycloakAdmin(settings.keycloak_url, settings.keycloak_realm, settings.keycloak_admin_client_id,
                       settings.keycloak_admin_client_secret)
    for username, group, first_name in DEMO_USERS:
        kc.create_user(username, f"{username}@datahub.local", password, [group], first_name=first_name)
        print(f"✔ {username} ({group})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
