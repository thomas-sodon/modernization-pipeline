#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys
from pathlib import Path


def main():
    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

    try:
        from django.core.management import execute_from_command_line  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(  # noqa: TRY003
            "Couldn't import Django. Are you sure it's installed and "  # noqa: EM101
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?",
        ) from exc

    current_path = Path(__file__).parent.resolve()
    # The parent of app/ holds the vendored equivalence package, which management
    # commands import (the pytest settings add it as pythonpath "..").
    if str(current_path.parent) not in sys.path:
        sys.path.append(str(current_path.parent))

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
