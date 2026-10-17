"""Feature-app discovery: apps register themselves, so nobody edits config/ again.

- discover_local_apps(APPS_DIR) returns sorted "<slug>.<name>" for every
  APPS_DIR/*/apps.py except users (settings list "<slug>.users" first). When
  MP_ONLY_AREAS is set and not empty (comma-separated area ids), only core, legacy
  and domain (those that exist) and the named areas are returned, chosen by
  directory name, so no other area's apps.py is imported; an id without a package
  raises ImproperlyConfigured. Unset or empty means every area.
- feature_app_configs() lists the installed AppConfigs with feature = True.
- feature_urlpatterns() includes "<name>.urls" at "<label>/" for each of them
  that has urls.py.
- feature_nav_items() gives the (nav_label, "/<label>/") pairs, sorted.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from django.core.exceptions import ImproperlyConfigured

if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.apps import AppConfig

ALWAYS = ("core", "legacy", "domain")
SKIP = ("users",)


def only_areas(environ_: Mapping[str, str] | None = None) -> list[str]:
    environ_ = os.environ if environ_ is None else environ_
    raw = (environ_.get("MP_ONLY_AREAS") or "").strip()
    return [part.strip() for part in raw.split(",") if part.strip()]


def discover_local_apps(
    apps_dir: Path | str,
    environ_: Mapping[str, str] | None = None,
) -> list[str]:
    apps_dir = Path(apps_dir)
    slug = apps_dir.name
    found = sorted(
        path.parent.name
        for path in apps_dir.glob("*/apps.py")
        if path.parent.name not in SKIP
    )
    wanted = only_areas(environ_)
    if wanted:
        missing = sorted(set(wanted) - set(found))
        if missing:
            msg = (
                "MP_ONLY_AREAS names areas without a package in "
                f"{slug}/: {', '.join(missing)}"
            )
            raise ImproperlyConfigured(msg)
        found = [name for name in found if name in ALWAYS or name in wanted]
    return [f"{slug}.{name}" for name in found]


def feature_app_configs() -> list[AppConfig]:
    from django.apps import apps  # noqa: PLC0415

    return sorted(
        (cfg for cfg in apps.get_app_configs() if getattr(cfg, "feature", False)),
        key=lambda cfg: cfg.label,
    )


def feature_urlpatterns() -> list:
    from django.urls import include  # noqa: PLC0415
    from django.urls import path  # noqa: PLC0415

    return [
        path(f"{cfg.label}/", include(f"{cfg.name}.urls"))
        for cfg in feature_app_configs()
        if (Path(cfg.path) / "urls.py").is_file()
    ]


def feature_nav_items() -> list[tuple[str, str]]:
    return sorted(
        (str(getattr(cfg, "nav_label", cfg.verbose_name)), f"/{cfg.label}/")
        for cfg in feature_app_configs()
    )
