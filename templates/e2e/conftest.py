"""End-to-end set-up: a real browser against pytest-django's live_server on seed 1.

This is the pipeline's templates/e2e/conftest.py, copied unchanged into the app's
tests/e2e/ (templates/e2e/README.md). Every test in tests/e2e/:

- runs with DJANGO_ALLOW_ASYNC_UNSAFE=true (set when this module is imported, before
  any test runs), because Playwright's sync API runs an event loop in the test thread
  and Django refuses ORM calls from inside one otherwise;
- gets a 1280x800 viewport and the live server as the context's base_url, so
  page.goto("/<area>/") works;
- starts on synthetic seed 1 (core.testing load_seed(1)) plus the demo users of
  create_demo_users (one per role, <role>@example.com), whose password is a fresh
  secrets.token_urlsafe() value for this process, never printed or written.

Fixtures: sign_in(page, role), shot(page, name), pause(page) and record_context(name).
Screenshots are taken only when E2E_SCREENSHOT_DIR is set and the video is recorded
only when E2E_VIDEO_DIR is set, so an ordinary run (CI included) writes no file.
"""

from __future__ import annotations

import contextlib
import io
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from django.core.management import call_command

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterator

    from playwright.sync_api import Page

os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

SEED = 1
VIEWPORT = {"width": 1280, "height": 800}
SCREENSHOT_DIR = "E2E_SCREENSHOT_DIR"
VIDEO_DIR = "E2E_VIDEO_DIR"
MAX_SCREENSHOT_BYTES = 400 * 1024
DEMO_DOMAIN = "example.com"
LOGIN_URL = "/accounts/login/"
# Pause between walkthrough steps, so a viewer of the video can follow.
WALKTHROUGH_PAUSE_MS = 1200


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "walkthrough: the recorded demo walkthrough; run alone with "
        "`-m walkthrough tests/e2e`",
    )


@pytest.fixture(scope="session")
def demo_password() -> str:
    """The demo users' password for this process: random, set in DEMO_PASSWORD."""
    password = secrets.token_urlsafe()
    os.environ["DEMO_PASSWORD"] = password
    return password


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, live_server):
    return {**browser_context_args, "viewport": VIEWPORT, "base_url": live_server.url}


@pytest.fixture(autouse=True)
def seed_and_demo_users(live_server, transactional_db, load_seed, demo_password):
    """Every e2e test starts on seed 1 with one signed-up user per role."""
    load_seed(SEED)
    call_command("create_demo_users", stdout=io.StringIO())


@pytest.fixture
def sign_in(demo_password) -> Callable[..., None]:
    """sign_in(page, role): sign in on the allauth page as <role>@example.com.

    With next_url, the sign-in page is reached by opening that URL signed out, so
    the user lands where the app sends them after signing in.
    """

    def _sign_in(page: Page, role: str, *, next_url: str | None = None) -> None:
        page.goto(next_url or LOGIN_URL)
        page.locator("input[name=login]").fill(f"{role}@{DEMO_DOMAIN}")
        page.locator("input[name=password]").fill(demo_password)
        page.locator("form").filter(has=page.locator("input[name=password]")).locator(
            "button[type=submit]",
        ).click()
        page.wait_for_load_state()

    return _sign_in


@pytest.fixture
def shot() -> Callable[[Page, str], Path | None]:
    """shot(page, name): <name>.png of the viewport in $E2E_SCREENSHOT_DIR, if set."""

    def _shot(page: Page, name: str) -> Path | None:
        directory = os.environ.get(SCREENSHOT_DIR)
        if not directory:
            return None
        path = Path(directory) / f"{name}.png"
        page.wait_for_load_state("networkidle")
        page.screenshot(path=path, full_page=False)
        size = path.stat().st_size
        assert size <= MAX_SCREENSHOT_BYTES, f"{path.name} is {size} bytes"
        return path

    return _shot


@pytest.fixture
def pause() -> Callable[[Page], None]:
    """pause(page): wait long enough for a viewer to follow (walkthrough only)."""

    def _pause(page: Page) -> None:
        page.wait_for_timeout(WALKTHROUGH_PAUSE_MS)

    return _pause


@pytest.fixture
def record_context(new_context) -> Callable[[str], contextlib.AbstractContextManager]:
    """record_context("walkthrough"): a page in its own browser context.

    When E2E_VIDEO_DIR is set the context is recorded at 1280x800; on exit the
    context is closed, the video saved as <name>.webm in that directory, Playwright's
    randomly named copy deleted and the file checked to be non-empty.
    """

    @contextlib.contextmanager
    def _record(name: str) -> Iterator[Page]:
        directory = os.environ.get(VIDEO_DIR)
        options = {}
        if directory:
            options = {"record_video_dir": directory, "record_video_size": VIEWPORT}
        context = new_context(**options)
        page = context.new_page()
        try:
            yield page
        finally:
            video = page.video
            context.close()
            if directory and video is not None:
                target = Path(directory) / f"{name}.webm"
                video.save_as(target)
                video.delete()
                assert target.stat().st_size > 0, f"{target.name} is empty"

    return _record
