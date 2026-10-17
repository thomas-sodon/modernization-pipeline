# Django overlay

What `toolkit new-app` lays over a cookiecutter-django 2026.03.09 project
(docs/contracts.md#feature-app-contract). Nothing here is engagement-specific: values come from
the engagement when files are rendered.

| Directory | Goes to | Applied by |
|---|---|---|
| `project/` | `app/` | every `new-app` run |
| `package/` | `app/<slug>/` | every `new-app` run |
| `core/` | `app/<slug>/core/` (never `core/local/`) | `new-app --only core` (owned by S4.data) |
| `_repo/` | the target repo (`workflow.yml.j2` becomes `.github/workflows/<slug>.yml`) | every `new-app` run; never copied into `app/` |

Files ending `.j2` are rendered with Jinja2 (StrictUndefined) and lose the suffix; every other
file is copied as it is. Render variables: `slug`, `project_name`, `db_name`, `target_root`,
`timezone`, `use_tz`, `cloud_provider`, `uv_version`, `postgres_image`, `mssql_image`. Django
templates such as `package/templates/base.html` are not `.j2`, so their `{{ }}` and `{% %}` stay
as they are; inside a `.j2` file wrap Django or GitHub Actions syntax in `{% raw %}`.

What the overlay changes:
- `config/`: settings that read `DATABASE_URL` or fall back to `$PG_URL/<db_name>`
  (`database.py`), feature-app discovery with `MP_ONLY_AREAS` (`discovery.py`), test-database
  names (`testdb.py`), the feature nav context processor, `GET /health/`, URL discovery, and
  production settings without Redis or anymail.
- `conftest.py`, `manage.py`, `tests/test_overlay.py`: test-database naming, the `core.testing`
  plugin, the equivalence package on `sys.path`, and the overlay's own tests.
- `package/`: `base.html` (htmx 2 through django-htmx, feature nav, print stylesheet),
  `static/css/print.css`, the users adapters (closed registration, Microsoft sign-in with the
  Entra group to role mapping), and the sites migration whose sequence statements run only on
  PostgreSQL.

Every rendered file must pass the app's ruff (line length 88) and `ruff format --check`; the
app's tests prove the behaviour on PostgreSQL and SQL Server.
