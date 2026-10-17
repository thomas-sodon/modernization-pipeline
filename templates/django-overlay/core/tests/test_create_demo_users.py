"""manage.py create_demo_users: one staff user per role in that role's group, the
superuser, verified e-mail addresses, the password from DEMO_PASSWORD only."""

from __future__ import annotations

import io

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from .. import roles  # noqa: TID252

PASSWORD = "demo-password-for-tests"  # noqa: S105


def run() -> str:
    out = io.StringIO()
    call_command("create_demo_users", stdout=out)
    return out.getvalue()


@pytest.mark.django_db
def test_without_demo_password_it_exits_1(monkeypatch):
    monkeypatch.delenv("DEMO_PASSWORD", raising=False)
    with pytest.raises(CommandError) as info:
        run()
    assert info.value.returncode == 1
    assert not get_user_model().objects.exists()


@pytest.mark.django_db
def test_one_staff_user_per_role_and_the_superuser(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", PASSWORD)
    out = run()
    user_model = get_user_model()
    emails = [f"{role}@example.com" for role in roles.role_names()]
    assert out.split() == [*emails, "admin@example.com"]
    assert PASSWORD not in out
    for role, email in zip(roles.role_names(), emails, strict=True):
        user = user_model.objects.get(email=email)
        assert user.is_staff
        assert not user.is_superuser
        assert list(user.groups.values_list("name", flat=True)) == [role]
        assert user.check_password(PASSWORD)
        assert user.get_all_permissions() == roles.role_permissions(role)
    admin = user_model.objects.get(email="admin@example.com")
    assert admin.is_superuser
    assert admin.is_staff
    for email in [*emails, "admin@example.com"]:
        address = EmailAddress.objects.get(email=email)
        assert address.verified
        assert address.primary


@pytest.mark.django_db
def test_running_twice_changes_nothing_but_the_password(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", PASSWORD)
    run()
    users = get_user_model().objects.count()
    addresses = EmailAddress.objects.count()
    monkeypatch.setenv("DEMO_PASSWORD", PASSWORD + "-2")
    run()
    assert get_user_model().objects.count() == users
    assert EmailAddress.objects.count() == addresses
    admin = get_user_model().objects.get(email="admin@example.com")
    assert admin.check_password(PASSWORD + "-2")
