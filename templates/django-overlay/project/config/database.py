"""The default database from the environment (PostgreSQL or SQL Server).

- DATABASE_URL when set (postgres://, postgresql:// or mssql://; django-environ
  maps mssql:// to ENGINE "mssql").
- Otherwise PG_URL (a server URL without a database) plus "/<DB_NAME>", so a fresh
  checkout without the gitignored app/.env still finds the app dev database.
- Neither: ImproperlyConfigured naming both variables. No Unix-socket default.

SQL Server URLs get OPTIONS python_driver "mssql_python" (mssql-django then uses the
mssql-python driver, which bundles its own ODBC driver) and, for a local server,
extra_params TrustServerCertificate=yes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import environ
from django.core.exceptions import ImproperlyConfigured

if TYPE_CHECKING:
    from collections.abc import Mapping

LOCAL_HOSTS = ("127.0.0.1", "localhost")


def database_url(environ_: Mapping[str, str], db_name: str) -> str:
    """The URL the app connects to: DATABASE_URL, else $PG_URL/<db_name>."""
    url = (environ_.get("DATABASE_URL") or "").strip()
    if url:
        return url
    server = (environ_.get("PG_URL") or "").strip()
    if server:
        return f"{server.rstrip('/')}/{db_name}"
    msg = (
        "No database configured: set DATABASE_URL (postgres://... or mssql://...), "
        "or PG_URL (a server URL; the app adds /" + db_name + ")."
    )
    raise ImproperlyConfigured(msg)


def database_config(url: str) -> dict:
    """django-environ's settings for url, with the SQL Server options added."""
    config = environ.Env.db_url_config(url)
    if config.get("ENGINE") == "mssql":
        options = config.setdefault("OPTIONS", {})
        options["python_driver"] = "mssql_python"
        if (urlsplit(url).hostname or "").lower() in LOCAL_HOSTS:
            options["extra_params"] = "TrustServerCertificate=yes"
    return config


def database_settings(environ_: Mapping[str, str], db_name: str) -> dict:
    return database_config(database_url(environ_, db_name))
