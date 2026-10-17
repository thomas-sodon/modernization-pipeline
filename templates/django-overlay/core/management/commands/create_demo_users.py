"""manage.py create_demo_users (docs/contracts.md#feature-app-contract).

One is_staff user per role of core/local/roles.py ROLES, <role>@example.com, in that
role's group only, so the admin shows exactly what the role may see; plus the
superuser admin@example.com. Every user gets a verified primary allauth
EmailAddress, so sign-in works with mandatory e-mail verification. The password
comes from DEMO_PASSWORD (exit 1 when it is unset); it is never stored in the repo
or printed. Running it again updates the same users (idempotent).
"""

import os

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction

from ...roles import role_group  # noqa: TID252
from ...roles import role_names  # noqa: TID252
from ...roles import sync_roles  # noqa: TID252

ADMIN_EMAIL = "admin@example.com"
DOMAIN = "example.com"


def demo_email(role: str) -> str:
    return f"{role}@{DOMAIN}"


def verify_email(user) -> None:
    """A verified primary EmailAddress for the user's e-mail (and no other primary)."""
    EmailAddress.objects.filter(user=user).exclude(email__iexact=user.email).update(
        primary=False,
    )
    address, _created = EmailAddress.objects.get_or_create(
        user=user,
        email=user.email,
        defaults={"verified": True, "primary": True},
    )
    if not (address.verified and address.primary):
        address.verified = True
        address.primary = True
        address.save(update_fields=["verified", "primary"])


class Command(BaseCommand):
    help = (
        "Create or update one staff user per role (<role>@example.com) and the "
        "superuser admin@example.com; the password comes from DEMO_PASSWORD."
    )

    def handle(self, *args, **options):
        password = os.environ.get("DEMO_PASSWORD") or ""
        if not password:
            msg = "DEMO_PASSWORD is not set; it holds the demo users' password"
            raise CommandError(msg, returncode=1)
        user_model = get_user_model()
        made: list[str] = []
        with transaction.atomic():
            sync_roles()
            for role in role_names():
                user = self._user(user_model, demo_email(role), password)
                user.is_staff = True
                user.is_superuser = False
                user.is_active = True
                user.save()
                user.groups.set([role_group(role)])
                verify_email(user)
                made.append(user.email)
            admin = self._user(user_model, ADMIN_EMAIL, password)
            admin.is_staff = True
            admin.is_superuser = True
            admin.is_active = True
            admin.save()
            verify_email(admin)
            made.append(admin.email)
        for email in made:
            self.stdout.write(email)

    @staticmethod
    def _user(user_model, email: str, password: str):
        field = user_model.USERNAME_FIELD
        user = user_model.objects.filter(**{f"{field}__iexact": email}).first()
        if user is None:
            user = user_model(**{field: email})
            if field != "email" and hasattr(user, "email"):
                user.email = email
        user.set_password(password)
        return user
