"""Authentication: Keycloak OpenID Connect through Streamlit's native `st.login()`.

The Keycloak client `datahub-ui` has a protocol mapper that writes the user's realm
roles into the ID token claim `roles` (and groups into `groups`), which Streamlit
exposes in `st.user`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from datahub_ui.config import Settings
from datahub_ui.rbac import Perm, PermissionDenied, Role, highest_role, normalize_roles, permissions_for

OIDC_PROVIDER = "keycloak"


@dataclass(frozen=True)
class User:
    username: str
    email: str = ""
    name: str = ""
    roles: frozenset[Role] = field(default_factory=frozenset)
    groups: tuple[str, ...] = ()
    claims: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def permissions(self) -> frozenset[Perm]:
        return permissions_for(self.roles)

    @property
    def main_role(self) -> Role | None:
        return highest_role(self.roles)

    def can(self, perm: Perm) -> bool:
        return perm in self.permissions

    def require(self, perm: Perm) -> None:
        if not self.can(perm):
            raise PermissionDenied(perm)


def user_from_claims(claims: dict) -> User:
    roles = claims.get("roles") or (claims.get("realm_access") or {}).get("roles") or []
    groups = tuple(g.lstrip("/") for g in claims.get("groups") or [])
    return User(
        username=claims.get("preferred_username") or claims.get("email") or claims.get("sub") or "inconnu",
        email=claims.get("email", ""),
        name=claims.get("name") or claims.get("preferred_username", ""),
        roles=normalize_roles(roles),
        groups=groups,
        claims=dict(claims),
    )


def dev_user(role: str) -> User:
    return User(
        username=f"dev-{role.lower()}",
        email="dev@localhost",
        name=f"Développeur ({role})",
        roles=normalize_roles([role]),
        groups=("dev",),
    )


def current_user(settings: Settings, st_module=None) -> User | None:
    """Return the logged-in user, or None when the login screen must be shown."""
    import streamlit as st

    st = st_module or st
    if settings.auth_mode == "none":
        role = st.session_state.get("dev_role", settings.dev_default_role)
        return dev_user(role)
    if not st.user.is_logged_in:
        return None
    return user_from_claims(st.user.to_dict())
