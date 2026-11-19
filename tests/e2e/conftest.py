"""End-to-end tests of a running Data Hub stack, through the public entry point (Caddy).

Required environment:
    DATAHUB_PUBLIC_URL        e.g. http://datahub.local
    DEMO_USERS_PASSWORD       password of the demo users (scripts/seed-demo-users)
    STREAMLIT_COOKIE_SECRET   to decode the Streamlit auth cookie and check the OIDC claims
"""

import html
import os
import re
import time

import pytest
import requests

PUBLIC_URL = os.getenv("DATAHUB_PUBLIC_URL", "").rstrip("/")
PASSWORD = os.getenv("DEMO_USERS_PASSWORD", "")

if not PUBLIC_URL or not PASSWORD:
    pytest.skip("DATAHUB_PUBLIC_URL / DEMO_USERS_PASSWORD not set (stack not running)", allow_module_level=True)

USERS = {
    "ADMIN": "alice.admin",
    "DATA_ENGINEER": "eric.engineer",
    "ANALYST": "anna.analyst",
    "VIEWER": "victor.viewer",
}
FORM_RE = re.compile(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', re.S)


def new_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False  # never send local traffic through a corporate proxy
    return session


def keycloak_login(session: requests.Session, start_url: str, username: str, password: str = PASSWORD) -> requests.Response:
    """Browser-like authorization code flow: start URL -> Keycloak login form -> POST -> redirects back."""
    page = session.get(start_url, timeout=30)
    match = FORM_RE.search(page.text)
    assert match, f"Keycloak login form not found at {page.url} (HTTP {page.status_code})"
    return session.post(html.unescape(match.group(1)), data={"username": username, "password": password},
                        timeout=30, allow_redirects=True)


def wait_for(url: str, timeout: int = 300, ok=(200,)) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = requests.get(url, timeout=10, proxies={"http": None, "https": None}).status_code
            if last in ok:
                return
        except requests.RequestException as exc:
            last = str(exc)
        time.sleep(3)
    pytest.fail(f"{url} not ready after {timeout}s (last: {last})")


@pytest.fixture(scope="session", autouse=True)
def stack_ready():
    wait_for(f"{PUBLIC_URL}/_stcore/health")
    wait_for(f"{PUBLIC_URL}/auth/realms/datahub/.well-known/openid-configuration")
