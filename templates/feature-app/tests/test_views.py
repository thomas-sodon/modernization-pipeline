"""Every screen of the __area_title__ area: an anonymous user is sent to the sign-in
page, a user without the role gets 403 and a user with the role gets 200 (a PDF body
starts with %PDF-).

The contract's form of this file is one parametrize table that names every rules.json
object of the area with type form or report and decision keep
(docs/contracts.md#feature-app-contract): replace the example rows with them.
"""

from http import HTTPStatus
from typing import NamedTuple

import pytest
from django.conf import settings
from django.shortcuts import resolve_url
from django.urls import reverse

from __slug__.core.roles import role_names
from __slug__.core.roles import role_permissions
from __slug__.legacy.factories import FACTORIES

# isort: split
# (the area's own modules sort after the shared ones, whatever the area id)
from __slug__.__area__ import services

pytestmark = pytest.mark.django_db


class Screen(NamedTuple):
    source: str  # the original form or report
    url_name: str
    action: str  # the permission the view checks: view, add or change
    query: str = ""


SCREENS = [
    Screen("frmExampleList", "list", "view"),
    Screen("frmExampleDetail", "detail", "view"),
    Screen("frmExampleDetail", "create", "add"),
    Screen("frmExampleDetail", "update", "change"),
    Screen("rptExample", "print", "view"),
    Screen("rptExample", "print", "view", "?format=pdf"),
]
NEEDS_RECORD = {"detail", "update", "print"}


def screen_id(screen: Screen) -> str:
    return f"{screen.source}-{screen.url_name}{screen.query}"


@pytest.fixture
def record():
    return FACTORIES[services.Record].create()


def url_of(screen: Screen, record) -> str:
    kwargs = {"pk": record.pk} if screen.url_name in NEEDS_RECORD else {}
    return reverse(f"__area__:{screen.url_name}", kwargs=kwargs) + screen.query


def role_granting(action: str) -> str:
    """A role whose group holds the permission the screen checks."""
    opts = services.Record._meta  # noqa: SLF001 (Django's documented Options API)
    permission = f"{opts.app_label}.{action}_{opts.model_name}"
    return next(role for role in role_names() if permission in role_permissions(role))


@pytest.mark.parametrize("screen", SCREENS, ids=screen_id)
def test_anonymous_user_is_sent_to_sign_in(client, record, screen):
    response = client.get(url_of(screen, record))
    assert response.status_code == HTTPStatus.FOUND
    assert response.url.startswith(resolve_url(settings.LOGIN_URL))


@pytest.mark.parametrize("screen", SCREENS, ids=screen_id)
def test_user_without_the_role_is_refused(client, user, record, screen):
    client.force_login(user)
    response = client.get(url_of(screen, record))
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.parametrize("screen", SCREENS, ids=screen_id)
def test_user_with_the_role_gets_the_screen(client, user_with_role, record, screen):
    client.force_login(user_with_role(role_granting(screen.action)))
    response = client.get(url_of(screen, record))
    assert response.status_code == HTTPStatus.OK
    if "format=pdf" in screen.query:
        assert response.content.startswith(b"%PDF-")


def test_htmx_list_request_gets_only_the_table(client, user_with_role, record):
    client.force_login(user_with_role(role_granting("view")))
    response = client.get(
        url_of(Screen("frmExampleList", "list", "view"), record),
        headers={"HX-Request": "true"},
    )
    assert response.status_code == HTTPStatus.OK
    assert b'id="record-table"' in response.content
    assert b"<html" not in response.content
