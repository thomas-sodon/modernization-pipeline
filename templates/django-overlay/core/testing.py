"""pytest fixtures of the core (a plugin module: app/conftest.py lists it in
pytest_plugins, so tests anywhere under app/, tests/e2e/ included, reach it).

- load_seed: `load_seed(n)` loads goldens/synthetic/seed-<n>/input (resolved from
  settings.BASE_DIR.parent / "goldens/synthetic") into the test database through the
  import_dataset loader, flushing the legacy tables first, and returns the
  LoadResult (per-table counts). It requests pytest-django's db, so it also works
  with transactional_db and live_server.
- user_with_role: `user_with_role(role)` returns a new active user with a verified
  primary e-mail address (allauth EmailAddress) in that role's group
  (core/local/roles.py ROLES). `user_with_role(role, password="...")` sets a
  password for sign-in tests.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

SEEDS_DIR = "goldens/synthetic"


def seed_directory(n: int | str) -> Path:
    """goldens/synthetic/seed-<n>/input next to the app folder."""
    from django.conf import settings  # noqa: PLC0415

    return Path(settings.BASE_DIR).parent / SEEDS_DIR / f"seed-{n}" / "input"


@pytest.fixture
def load_seed(db):
    """Load a synthetic seed into the test database: load_seed(1)."""
    from .loader import load_dataset  # noqa: PLC0415

    def load(n: int | str = 1):
        directory = seed_directory(n)
        if not (directory / "dataset.json").is_file():
            msg = f"seed-{n} has no dataset: {directory}"
            raise FileNotFoundError(msg)
        return load_dataset(directory, flush=True)

    return load


_counter = itertools.count(1)


@pytest.fixture
def user_with_role(db, django_user_model):
    """A user in a role's group with a verified e-mail: user_with_role("staff")."""
    from allauth.account.models import EmailAddress  # noqa: PLC0415

    from .roles import role_group  # noqa: PLC0415

    def make(role: str, *, password: str | None = None, email: str | None = None):
        group = role_group(role)
        email = email or f"{role}-{next(_counter)}@example.com"
        field = django_user_model.USERNAME_FIELD
        user = django_user_model(**{field: email})
        if field != "email" and hasattr(user, "email"):
            user.email = email
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        user.groups.add(group)
        EmailAddress.objects.create(
            user=user,
            email=email,
            verified=True,
            primary=True,
        )
        return user

    return make
