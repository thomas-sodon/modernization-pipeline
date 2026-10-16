from config.discovery import feature_nav_items


def feature_nav(request):
    """The project name and the feature apps' (nav_label, "/<label>/") pairs, sorted."""
    from django.conf import settings  # noqa: PLC0415

    return {
        "project_name": getattr(settings, "PROJECT_NAME", ""),
        "feature_nav": feature_nav_items(),
    }
