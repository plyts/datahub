"""Keycloak Admin REST API client (service account `datahub-admin-api`, client credentials).

Used by the Administration and IAM modules (ADMIN only): users, realm roles, sessions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import requests

from datahub_ui.rbac import Role


class KeycloakError(RuntimeError):
    pass


@dataclass
class KeycloakAdmin:
    base_url: str
    realm: str
    client_id: str
    client_secret: str
    timeout: float = 10.0
    _token: str | None = field(default=None, repr=False)
    _token_exp: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.client_secret)

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 30:
            return self._token
        try:
            response = requests.post(
                f"{self.base_url}/realms/{self.realm}/protocol/openid-connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise KeycloakError(f"Authentification du compte de service impossible : {exc}") from exc
        payload = response.json()
        self._token = payload["access_token"]
        self._token_exp = time.time() + payload.get("expires_in", 60)
        return self._token

    def _request(self, method: str, path: str, **kwargs):
        try:
            response = requests.request(
                method,
                f"{self.base_url}/admin/realms/{self.realm}{path}",
                headers={"Authorization": f"Bearer {self._access_token()}"},
                timeout=self.timeout,
                **kwargs,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise KeycloakError(f"Keycloak {method} {path} : {exc}") from exc
        return response.json() if response.content else None

    def ready(self) -> bool:
        try:
            return requests.get(f"{self.base_url}/realms/{self.realm}", timeout=5).status_code == 200
        except requests.RequestException:
            return False

    def users(self, search: str = "", max_results: int = 200) -> list[dict]:
        params = {"max": max_results, "briefRepresentation": "false"}
        if search:
            params["search"] = search
        return self._request("GET", "/users", params=params)

    def user_realm_roles(self, user_id: str) -> list[str]:
        """Effective realm roles (direct + inherited from groups)."""
        return [r["name"] for r in self._request("GET", f"/users/{user_id}/role-mappings/realm/composite")]

    def user_direct_roles(self, user_id: str) -> list[str]:
        return [r["name"] for r in self._request("GET", f"/users/{user_id}/role-mappings/realm")]

    def user_groups(self, user_id: str) -> list[str]:
        return [g["path"] for g in self._request("GET", f"/users/{user_id}/groups")]

    def user_sessions(self, user_id: str) -> list[dict]:
        return self._request("GET", f"/users/{user_id}/sessions")

    def realm_role(self, name: str) -> dict:
        return self._request("GET", f"/roles/{name}")

    def set_datahub_roles(self, user_id: str, roles: set[str]) -> None:
        """Make the user's *direct* Data Hub roles exactly `roles` (group roles and other realm roles are untouched)."""
        wanted = {r for r in roles if r in {x.value for x in Role}}
        current = {r for r in self.user_direct_roles(user_id) if r in {x.value for x in Role}}
        to_add = [self.realm_role(r) for r in sorted(wanted - current)]
        to_remove = [self.realm_role(r) for r in sorted(current - wanted)]
        if to_add:
            self._request("POST", f"/users/{user_id}/role-mappings/realm", json=to_add)
        if to_remove:
            self._request("DELETE", f"/users/{user_id}/role-mappings/realm", json=to_remove)

    def set_enabled(self, user_id: str, enabled: bool) -> None:
        self._request("PUT", f"/users/{user_id}", json={"enabled": enabled})

    def logout_user(self, user_id: str) -> None:
        self._request("POST", f"/users/{user_id}/logout")

    def find_user(self, username: str) -> dict | None:
        users = self._request("GET", "/users", params={"username": username, "exact": "true"})
        return users[0] if users else None

    def create_user(self, username: str, email: str, password: str, groups: list[str], first_name: str = "",
                    last_name: str = "") -> str:
        """Create (or update) a user with a non-temporary password and group memberships. Returns its id."""
        existing = self.find_user(username)
        body = {"username": username, "email": email, "emailVerified": True, "enabled": True,
                "firstName": first_name or username, "lastName": last_name or "Data Hub"}
        if existing:
            user_id = existing["id"]
            self._request("PUT", f"/users/{user_id}", json=body)
        else:
            self._request("POST", "/users", json=body)
            user_id = self.find_user(username)["id"]
        self._request("PUT", f"/users/{user_id}/reset-password",
                      json={"type": "password", "value": password, "temporary": False})
        all_groups = {g["path"]: g["id"] for g in self.groups()}
        for path in groups:
            self._request("PUT", f"/users/{user_id}/groups/{all_groups[path]}")
        return user_id

    def groups(self) -> list[dict]:
        return self._request("GET", "/groups")

    def client_sessions_count(self, client_id: str = "datahub-ui") -> int:
        clients = self._request("GET", "/clients", params={"clientId": client_id})
        if not clients:
            return 0
        return int(self._request("GET", f"/clients/{clients[0]['id']}/session-count").get("count", 0))
