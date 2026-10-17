# Feature-app skeleton

This directory is a skeleton Django app for one feature area. Every S5 feature task
copies it into the target app and builds its area on it. The layout and the rules
for an area are in [docs/contracts.md#feature-app-contract](../../docs/contracts.md#feature-app-contract).

The files contain three tokens:

| Token | Replaced with | Example |
|---|---|---|
| `__slug__` | the app slug (`engagement.json` `app.slug`) | `shop` |
| `__area__` | the area id (`analysis/rules.json` `areas[].id`) | `orders` |
| `__area_title__` | the area title (`areas[].title`) | `Orders and invoicing` |

The directory `templates/__area__/` is renamed to the area id.

## Copy it

Run these commands from the engagement root of the target repo (the directory that
holds `app/`). `<pipeline checkout>` is your checkout of the pipeline repo.

1. Copy the skeleton. Then remove this README and `ruff.toml` from the copy. The
   `ruff.toml` file only makes the pipeline lint the skeleton with the app's rules;
   the copy follows `app/pyproject.toml`:

   ```sh
   cp -r <pipeline checkout>/templates/feature-app app/<app slug>/<area id>
   rm app/<app slug>/<area id>/README.md app/<app slug>/<area id>/ruff.toml
   ```

2. Set `apps.py` aside, so the area stays uninstalled while it is half built (see
   the build order below):

   ```sh
   mv app/<app slug>/<area id>/apps.py app/<app slug>/<area id>/apps.py.last
   ```

3. Rename the templates directory:

   ```sh
   mv app/<app slug>/<area id>/templates/__area__ app/<app slug>/<area id>/templates/<area id>
   ```

4. Replace the three tokens in every file. Do the title first. In the title, escape
   any `&`, `/` or `\` with a backslash for sed:

   ```sh
   grep -rlE '__(slug|area|area_title)__' app/<app slug>/<area id> | xargs sed -i \
     -e 's/__area_title__/<area title>/g' \
     -e 's/__slug__/<app slug>/g' \
     -e 's/__area__/<area id>/g'
   ```

5. Check that no token is left. This command prints nothing when every token is
   replaced:

   ```sh
   grep -rnE '__(slug|area|area_title)__' app/<app slug>/<area id>
   ```

6. Reformat the templates with the app's djLint settings. The line breaks depend on
   the length of the area id, and the target repo's hooks and CI run
   `djlint --check`:

   ```sh
   cd app && uv run djlint <app slug>/<area id>/templates --reformat --profile=django
   ```

## Build order

Django installs a feature area only when its `apps.py` exists. The overlay finds
areas through `app/<app slug>/*/apps.py`. Once the area is installed, `core` imports
its `saved_queries.py` at start-up and `config/urls.py` imports its `urls.py`. From
then on, an import error in the area breaks every Django command, for every area. So
build in this order:

1. `services.py` and everything it imports. Point `Record` at the area's main legacy
   model. Fill `LIST_FIELDS`, `FORM_FIELDS` and `FILTER_FIELDS` for the example
   screens, then add the area's services.
2. `saved_queries.py` and the modules it imports. Register the area's units.
3. `tables.py`, `filters.py`, `forms.py`, `views.py`, `urls.py`, `templates/` and
   `reports/`.
4. `apps.py` last:

   ```sh
   mv app/<app slug>/<area id>/apps.py.last app/<app slug>/<area id>/apps.py
   ```

   Then run the area's acceptance commands. Each later change must leave the package
   importable.

## What each file shows

| File | Pattern |
|---|---|
| `apps.py` | The AppConfig with `feature = True` and `nav_label`. The overlay routes the area at `/<area id>/` and adds it to the navigation. |
| `urls.py` | `app_name` and the list, detail, create, update and print routes. The contract adds `form_<id>` and `report_<id>` names. |
| `saved_queries.py` | The core registry, with one example each for `saved_query`, `formula_output` and `document`. |
| `services.py` | A service that checks its rule, raises `ValidationError` naming the rule id, and writes inside `transaction.atomic()`. |
| `tables.py`, `filters.py`, `forms.py` | A django-tables2 table, a django-filter filter set, and a model form with a crispy-forms `FormHelper` (Bootstrap 5). |
| `views.py` | Sign-in plus the role check: core's roles are groups with permissions, so a user without the role gets 403. Also shows a filtered table list that returns only the table partial to HTMX, detail, create and update views that save through the services, and a print view with an A4 PDF through django-weasyprint (`?format=pdf`). |
| `templates/__area__/` | `list.html`, `detail.html`, `form.html` and `print.html` extending `base.html`. The table body is a django-template-partials partial (`list.html#record-table`). The print page loads local stylesheets only, so the PDF renders offline. |
| `reports/` | The area's report views and exports. |
| `tests/test_rules.py` | One test per rule, with the rule id first in the docstring and factory data. |
| `tests/test_views.py` | The access table: anonymous users go to sign-in, users without the role get 403, users with the role get 200, and a PDF starts with `%PDF-`. Also tests the HTMX partial. |

Shared calculations come from `app/<app slug>/domain/`. Its README lists them. Never
copy one into an area. If the skeleton lacks a pattern your area needs, solve it in
the area and report it as a note that starts with `skeleton:`. The next bootstrap
engagement folds those notes into this directory.
