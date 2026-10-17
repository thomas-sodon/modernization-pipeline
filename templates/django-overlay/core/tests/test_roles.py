"""Role sync: one Group per role of core/local/roles.py ROLES with exactly its
permissions, kept after migrate and after a flush."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.contrib.auth.models import Permission

from .. import roles  # noqa: TID252


def group_permissions(name: str) -> set[str]:
    group = Group.objects.get(name=name)
    return {
        f"{a}.{c}"
        for a, c in group.permissions.values_list("content_type__app_label", "codename")
    }


def test_every_role_has_a_label_permissions_and_rules():
    assert roles.roles(), "core/local/roles.py ROLES is empty"
    for name, info in roles.roles().items():
        assert name.isidentifier(), name
        assert info["label"]
        assert info["permissions"]
        assert info["rules"], f"{name} cites no rule"


@pytest.mark.django_db
def test_migrate_created_one_group_per_role_with_exactly_its_permissions():
    for name in roles.role_names():
        assert group_permissions(name) == roles.role_permissions(name), name


@pytest.mark.django_db
def test_sync_restores_exact_permissions_and_is_idempotent():
    name = roles.role_names()[0]
    group = Group.objects.get(name=name)
    stray = Permission.objects.exclude(pk__in=roles.role_permission_ids(name)).first()
    group.permissions.add(stray)
    group.permissions.remove(*list(group.permissions.all())[:1])
    first = roles.sync_roles()
    second = roles.sync_roles()
    assert first == second
    assert group_permissions(name) == roles.role_permissions(name)


@pytest.mark.django_db
def test_custom_permissions_exist():
    for spec, info in roles.custom_permissions().items():
        app_label, codename = spec.split(".")
        perm = Permission.objects.get(
            content_type__app_label=app_label,
            codename=codename,
        )
        assert perm.name == info["name"]


@pytest.mark.django_db
def test_history_models_are_not_in_wildcards():
    for name in roles.role_names():
        assert not any(".view_historical" in p for p in roles.role_permissions(name))


@pytest.mark.django_db(transaction=True)
def test_groups_come_back_after_a_flush():
    """TransactionTestCase flushes the tables and emits post_migrate again."""
    for name in roles.role_names():
        assert Group.objects.filter(name=name).exists()


def test_unknown_role_is_a_key_error():
    with pytest.raises(KeyError):
        roles.role_group("zz_no_such_role")
