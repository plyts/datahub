"""Streamlit script used by test_pages.py: renders one page with a dev user and fake backends."""

import os

from datahub_ui.auth import dev_user
from datahub_ui.config import get_settings
from datahub_ui.context import build_context
from datahub_ui.navigation import PAGES, render_function

spec = next(p for p in PAGES if p.module == os.environ["HARNESS_PAGE"])
ctx = build_context(get_settings(), dev_user(os.environ["HARNESS_ROLE"]))
render_function(spec)(ctx)
