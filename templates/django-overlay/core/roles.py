"""Roles that mirror the source application's privileges, as Django Groups.

core/local/roles.py (engagement code) defines:

- ROLES: {"<role>": {"label": str, "permissions": [spec, ...], "rules": [rule id, ...],
  "exclude": [spec, ...] (optional)}}. A spec is "<app_label>.<codename>" or
  "<app_label>.*" (every permission of that app's models; history models excluded).
- CUSTOM_PERMISSIONS (optional): {"<app_label>.<codename>": {"model":
  "<app_label>.<model>", "name": str, "rules": [rule id, ...]}}, permissions that no
  model Meta declares (an approval right, say). They are rows, not migrations.

sync_roles() creates or updates one Group per role, named after the role, with exactly
the role's permissions; groups not in ROLES are left alone. It runs after every
migrate (CoreConfig connects sync_roles_after_migrate to post_migrate), so a fresh
database, a test database and a flushed database all carry the role groups.
"""

from __future__ import annotations

import importlib
from typing import Any

from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.contrib.auth.models import Group
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS
from django.db import transaction
from simple_history.models import HistoricalChanges

LOCAL_ROLES = f"{__package__}.local.roles"


def _local() -> Any:
    try:
        return importlib.import_module(LOCAL_ROLES)
    except ModuleNotFoundError as exc:
        if exc.name in (LOCAL_ROLES, LOCAL_ROLES.rpartition(".")[0]):
            msg = f"{LOCAL_ROLES} is missing: it defines ROLES (engagement code)"
            raise ImproperlyConfigured(msg) from None
        raise


def roles() -> dict[str, dict[str, Any]]:
    """ROLES of core/local/roles.py."""
    return dict(_local().ROLES)


def custom_permissions() -> dict[str, dict[str, Any]]:
    """CUSTOM_PERMISSIONS of core/local/roles.py (empty when it defines none)."""
    return dict(getattr(_local(), "CUSTOM_PERMISSIONS", {}) or {})


def role_names() -> list[str]:
    return list(roles())


def _split(spec: str) -> tuple[str, str]:
    app_label, dot, codename = spec.partition(".")
    if not dot or not app_label or not codename:
        msg = f"permission {spec!r} is not '<app_label>.<codename>'"
        raise ImproperlyConfigured(msg)
    return app_label, codename


def _is_history(model: type | None) -> bool:
    return model is not None and issubclass(model, HistoricalChanges)


def ensure_custom_permissions(using: str = DEFAULT_DB_ALIAS) -> None:
    """Create or rename the CUSTOM_PERMISSIONS rows."""
    for spec, info in custom_permissions().items():
        _app_label, codename = _split(spec)
        model = global_apps.get_model(info["model"])
        ctype = ContentType.objects.db_manager(using).get_for_model(model)
        Permission.objects.using(using).update_or_create(
            content_type=ctype,
            codename=codename,
            defaults={"name": info["name"]},
        )


def _resolve(specs: list[str], using: str) -> set[int]:
    ids: set[int] = set()
    for spec in specs:
        app_label, codename = _split(spec)
        perms = Permission.objects.using(using).select_related("content_type")
        if codename == "*":
            found = [
                p
                for p in perms.filter(content_type__app_label=app_label)
                if not _is_history(p.content_type.model_class())
            ]
            if not found:
                msg = f"permission {spec!r}: the app has no permissions"
                raise ImproperlyConfigured(msg)
        else:
            found = list(
                perms.filter(content_type__app_label=app_label, codename=codename),
            )
            if len(found) != 1:
                msg = f"permission {spec!r} does not exist (run migrate)"
                raise ImproperlyConfigured(msg)
        ids.update(p.pk for p in found)
    return ids


def role_permission_ids(role: str, using: str = DEFAULT_DB_ALIAS) -> set[int]:
    """The ids of exactly the permissions of one role."""
    info = roles()[role]
    return _resolve(list(info.get("permissions") or []), using) - _resolve(
        list(info.get("exclude") or []),
        using,
    )


def role_permissions(role: str, using: str = DEFAULT_DB_ALIAS) -> set[str]:
    """'<app_label>.<codename>' of every permission of one role."""
    perms = Permission.objects.using(using).filter(
        pk__in=role_permission_ids(role, using),
    )
    return {
        f"{app_label}.{codename}"
        for app_label, codename in perms.values_list(
            "content_type__app_label",
            "codename",
        )
    }


def sync_roles(using: str = DEFAULT_DB_ALIAS) -> dict[str, int]:
    """One Group per role with exactly its permissions; returns permission counts."""
    counts: dict[str, int] = {}
    with transaction.atomic(using=using):
        ensure_custom_permissions(using)
        for name in roles():
            group, _created = Group.objects.using(using).get_or_create(name=name)
            ids = role_permission_ids(name, using)
            group.permissions.set(ids)
            counts[name] = len(ids)
    return counts


def role_group(role: str, using: str = DEFAULT_DB_ALIAS) -> Group:
    """The Group of one role (synced first when it does not exist yet)."""
    if role not in roles():
        msg = f"unknown role {role!r}; ROLES has {', '.join(roles())}"
        raise KeyError(msg)
    group = Group.objects.using(using).filter(name=role).first()
    if group is None:
        sync_roles(using)
        group = Group.objects.using(using).get(name=role)
    return group


def _last_app_with_models() -> str | None:
    labels = [c.label for c in global_apps.get_app_configs() if c.models_module]
    return labels[-1] if labels else None


def sync_roles_after_migrate(sender, using=DEFAULT_DB_ALIAS, **kwargs) -> None:
    """post_migrate handler: once per migrate, after the last app's signal."""
    if getattr(sender, "label", None) != _last_app_with_models():
        return
    for config in global_apps.get_app_configs():
        create_permissions(config, verbosity=0, using=using)
    sync_roles(using)
