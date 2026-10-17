"""The core.testing fixtures: load_seed (with db and with transactional_db) and
user_with_role."""

from __future__ import annotations

import json

import pytest
from allauth.account.models import EmailAddress

from .. import loader  # noqa: TID252
from .. import roles  # noqa: TID252
from ..testing import seed_directory  # noqa: TID252


def expected_counts() -> dict[str, int]:
    meta = json.loads((seed_directory(1) / "dataset.json").read_text("utf-8"))
    tables = loader.resources()
    return {t["name"]: t["rows"] for t in meta["tables"] if t["name"] in tables}


def test_load_seed_loads_seed_1(load_seed):
    result = load_seed(1)
    assert result.counts == expected_counts()


def test_load_seed_twice_flushes_first(load_seed):
    load_seed(1)
    result = load_seed(1)
    for table, rows in result.counts.items():
        assert loader.model_for(table)._base_manager.count() == rows  # noqa: SLF001


def test_load_seed_with_transactional_db(transactional_db, load_seed):
    result = load_seed(1)
    assert result.counts == expected_counts()


def test_load_seed_of_a_missing_seed(load_seed):
    with pytest.raises(FileNotFoundError):
        load_seed(999)


def test_user_with_role(user_with_role):
    for role in roles.role_names():
        user = user_with_role(role)
        assert list(user.groups.values_list("name", flat=True)) == [role]
        address = EmailAddress.objects.get(user=user)
        assert address.verified
        assert address.primary
        assert user.get_all_permissions() == roles.role_permissions(role)


def test_user_with_role_and_password(user_with_role):
    role = roles.role_names()[0]
    user = user_with_role(role, password="pw-for-tests")  # noqa: S106
    assert user.check_password("pw-for-tests")
