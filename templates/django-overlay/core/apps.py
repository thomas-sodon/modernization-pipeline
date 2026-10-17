"""CoreConfig: registries, roles and the dataset loader (no feature flag, no URLs).

ready() imports every installed app's saved_queries module, so the @saved_query and
@document registries fill themselves (installed apps only: MP_ONLY_AREAS limits it),
and connects the post_migrate handler that keeps one Django Group per role of
core/local/roles.py ROLES with exactly that role's permissions.
"""

from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.utils.module_loading import autodiscover_modules


class CoreConfig(AppConfig):
    name = __name__.rpartition(".")[0]
    label = "core"
    verbose_name = "Core"

    def ready(self) -> None:
        from .roles import sync_roles_after_migrate  # noqa: PLC0415

        autodiscover_modules("saved_queries")
        post_migrate.connect(
            sync_roles_after_migrate,
            dispatch_uid=f"{self.name}.sync_roles",
        )
