import pytest

from datahub_ui.auth import dev_user, user_from_claims
from datahub_ui.config import Settings
from datahub_ui.navigation import PAGES, visible_pages
from datahub_ui.rbac import ROLE_PERMISSIONS, Perm, PermissionDenied, Role, highest_role, normalize_roles, permissions_for


def test_admin_has_every_permission():
    assert ROLE_PERMISSIONS[Role.ADMIN] == frozenset(Perm)


@pytest.mark.parametrize(
    ("role", "allowed", "forbidden"),
    [
        (Role.DATA_ENGINEER, {Perm.PIPELINES_RUN, Perm.PIPELINES_EDIT, Perm.BACKFILL_RUN, Perm.LOGS_VIEW},
         {Perm.ADMIN_MANAGE, Perm.IAM_MANAGE}),
        (Role.ANALYST, {Perm.CATALOG_VIEW, Perm.LINEAGE_VIEW, Perm.QUALITY_VIEW},
         {Perm.PIPELINES_RUN, Perm.PIPELINES_EDIT, Perm.LOGS_VIEW, Perm.BACKFILL_RUN, Perm.CATALOG_EDIT}),
        (Role.VIEWER, {Perm.DASHBOARD_VIEW, Perm.PIPELINES_VIEW, Perm.PERF_VIEW},
         {Perm.PIPELINES_RUN, Perm.CATALOG_VIEW, Perm.DATA_PREVIEW, Perm.SCHEDULES_EDIT}),
    ],
)
def test_role_matrix_matches_specification(role, allowed, forbidden):
    perms = ROLE_PERMISSIONS[role]
    assert allowed <= perms
    assert not (forbidden & perms)


def test_normalize_roles_drops_keycloak_technical_roles():
    assert normalize_roles(["default-roles-datahub", "offline_access", "analyst", "VIEWER", 42]) == {Role.ANALYST, Role.VIEWER}


def test_permissions_union_and_highest_role():
    roles = {Role.ANALYST, Role.VIEWER}
    assert permissions_for(roles) == ROLE_PERMISSIONS[Role.ANALYST] | ROLE_PERMISSIONS[Role.VIEWER]
    assert highest_role(roles) == Role.ANALYST
    assert highest_role(set()) is None


def test_user_from_oidc_claims():
    user = user_from_claims({"preferred_username": "alice", "email": "a@x", "name": "Alice",
                             "roles": ["DATA_ENGINEER", "default-roles-datahub"], "groups": ["/data-engineers"]})
    assert user.username == "alice"
    assert user.roles == {Role.DATA_ENGINEER}
    assert user.groups == ("data-engineers",)
    assert user.can(Perm.PIPELINES_RUN)
    with pytest.raises(PermissionDenied):
        user.require(Perm.ADMIN_MANAGE)


def test_user_from_access_token_style_claims():
    user = user_from_claims({"sub": "123", "realm_access": {"roles": ["admin"]}})
    assert user.username == "123"
    assert user.main_role == Role.ADMIN


def test_user_without_datahub_role_has_no_permission():
    assert user_from_claims({"preferred_username": "bob", "roles": ["offline_access"]}).permissions == frozenset()


def test_dev_user():
    assert dev_user("VIEWER").roles == {Role.VIEWER}


@pytest.mark.parametrize(("env", "mode", "ok"), [("dev", "none", True), ("prod", "none", False),
                                                  ("staging", "none", False), ("prod", "oidc", True),
                                                  ("qa", "oidc", False), ("dev", "ldap", False)])
def test_settings_validation_forbids_unauthenticated_mode_outside_dev(env, mode, ok):
    assert (Settings(env=env, auth_mode=mode).validate() == []) is ok


def test_navigation_is_filtered_by_role():
    pages = {r: {p.url_path for p in visible_pages(dev_user(r.value))} for r in Role}
    assert pages[Role.ADMIN] == {p.url_path for p in PAGES}
    assert "admin" not in pages[Role.DATA_ENGINEER]
    assert {"catalog", "lineage", "quality"} <= pages[Role.ANALYST]
    assert "pipelines" not in pages[Role.ANALYST]
    assert pages[Role.VIEWER] == {"dashboard", "pipelines", "scheduling", "performance", "iam"}


def test_every_module_has_a_page():
    assert len([p for p in PAGES if p.module.startswith("m")]) == 11
