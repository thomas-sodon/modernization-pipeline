"""The __area_title__ area (docs/contracts.md#feature-app-contract).

The overlay installs every app/__slug__/*/apps.py package, mounts a feature app's
urls.py at /__area__/ and lists nav_label in the navigation. Copy this file in last:
until it exists, the area is not installed, so a half-built area cannot break the
other areas (README.md).
"""

from django.apps import AppConfig


class AreaConfig(AppConfig):
    name = "__slug__.__area__"
    label = "__area__"
    verbose_name = "__area_title__"
    feature = True
    nav_label = "__area_title__"
