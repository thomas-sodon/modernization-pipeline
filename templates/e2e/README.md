# End-to-end tests template

`conftest.py` here is the generic part of a target app's `app/tests/e2e/`: the set-up
that every engagement's browser tests share. S5.e2e copies it unchanged and writes
one test module per workflow of `analysis/rules.json` beside it. The contract is in
[docs/contracts.md#feature-app-contract](../../docs/contracts.md#feature-app-contract)
(target CI) and the S5.e2e build spec.

What `conftest.py` gives every test:

| Piece | What it does |
|---|---|
| `DJANGO_ALLOW_ASYNC_UNSAFE=true` | Set on import, before any test runs. Playwright's sync API runs an event loop in the test thread, and Django refuses ORM calls inside one otherwise. |
| `browser_context_args` | A 1280x800 viewport and the live server as `base_url`, so `page.goto("/<area>/")` works. |
| `seed_and_demo_users` (autouse) | Requests `live_server` and `transactional_db`, loads synthetic seed 1 with `core.testing` `load_seed(1)` and runs `create_demo_users` (one user per role, `<role>@example.com`, plus `admin@example.com`). |
| `demo_password` | A fresh `secrets.token_urlsafe()` per process, put in `DEMO_PASSWORD` for `create_demo_users`. It is never printed or written. |
| `sign_in(page, role, next_url=None)` | Signs in on the allauth page as the role's demo user. With `next_url`, opens that URL signed out first, so the user lands where the app sends them. |
| `shot(page, name)` | Saves `<name>.png` of the viewport (not the full page) in `$E2E_SCREENSHOT_DIR`, only when that variable is set, and fails above 400 KB. |
| `pause(page)` | Waits 1.2 s, so a viewer of the walkthrough video can follow. |
| `htmx_ready(page, selector)` | Waits until htmx has processed the element (its `htmx-internal-data.initHash` is set). Call it before you change a field of a form with `hx-trigger="change"` right after a page load; until htmx has processed the form the change event is lost and the test fails at random. |
| `record_context(name)` | A context manager giving a page in its own browser context. With `$E2E_VIDEO_DIR` set it records at 1280x800; on exit it closes the context, saves `<name>.webm` there with `video.save_as`, deletes Playwright's randomly named copy with `video.delete()` and checks the file is not empty. |
| `walkthrough` marker | Registered, so `-m walkthrough` selects the recorded walkthrough. |

It uses only pytest-playwright's own fixtures (`page`, `new_context`,
`browser_context_args`), pytest-django's `live_server` and `transactional_db`, and
Playwright's own `expect_download`, `record_video_dir` and `page.screenshot`. No
third-party e2e helpers.

## Copy it

From the engagement root of the target repo (the directory that holds `app/`):

```sh
mkdir -p app/tests/e2e
cp <pipeline checkout>/templates/e2e/conftest.py app/tests/e2e/conftest.py
touch app/tests/e2e/__init__.py
```

Copy `conftest.py` only. `ruff.toml` here makes the pipeline lint the template with
the app's rules; the copy follows `app/pyproject.toml`. The `__init__.py` keeps ruff's
INP001 quiet and lets the test modules import a shared helpers module as
`tests.e2e.helpers` (the app's pytest runs with `--import-mode=importlib` and `.` on
`pythonpath`).

## One module per workflow

For each `workflows[]` entry of `analysis/rules.json`, write
`app/tests/e2e/test_<workflow id>.py`. Every character of the id outside `[a-z0-9_]`
becomes `_`.

- **Docstring.** It names the workflow id, the `scope.must_keep` item it covers and
  every rule id it proves. Every rule with `test_kind` `e2e` in a kept area must
  appear in one docstring. The acceptance greps for the ids.
- **Steps.** Each step of the workflow's `steps` is a page action followed by an
  assertion on what the user sees: a total, a status or a stock level. Assert with
  `expect(...)`, which waits, rather than by reading text once.
- **Expected values.** Work them out from what the test entered and from figures the
  screens showed before the step. Do not hard-code seed values.
- **Labels.** Synthetic seeds scramble lookup labels. Read a status name from its
  table by the fixed code (`legacy.choices`) instead of spelling it.
- **Records.** Pick the records a flow needs by criteria, in a fixed order (say, the
  first product by name with several vendors and less to sell than its target). Fail
  with a clear message when the seed has none. When the seed lacks a state a step
  starts from (an invoiced order, say), arrange it through the area's services and
  say so in the docstring. The screens for that state belong to another workflow's
  test.
- **Downloads.** Check them in memory. Use
  `with page.expect_download() as d: ...click()`, then read `d.value.path()`, which
  is Playwright's own temporary copy. A PDF starts with `%PDF-`, an xlsx opens with
  openpyxl and an .eml parses with the `email` package. Never save into either repo.
  Headless Chromium downloads PDFs that are served inline.
- **Navigation.** Navigate the way a user does: the main menu, list rows, buttons.
  After clicking a list row, check that the record's own screen opened as a whole
  page. An `hx-boost` container around a list's table can otherwise swap the detail
  page into the list. A shared helper in `tests/e2e/helpers.py` can assert exactly one
  navigation bar.
- **Roles.** Sign in as the least-privileged role that can do a step. Sign out and
  sign in as another role when a step needs a privilege, such as an approval.
- **Signed-in person.** Some steps record the signed-in user as a person in the
  app's data, such as the submitter or approver who must be an employee. The demo
  users of `create_demo_users` are sign-ins only. They do not match such a record.
  For those steps, a helper in `tests/e2e/helpers.py` gives a seed record the role's
  sign-in e-mail before signing in, as an administrator would. Call it in the test,
  after the seed has loaded, and say so in the docstring or a comment. Do not change
  `conftest.py` for it.

## Screenshots

Take them with `shot(page, name)`, only for the steps worth showing:

- `<workflow id>-<NN>-<step>.png`, at least one per workflow. `NN` is the
  two-digit step number and `<step>` a few words in kebab case, for example
  `order_to_ship-05-invoice.png`.
- `<area id>-main.png` for each kept area's main screen.

They must be PNG, 1280x800 (the viewport, not the full page) and at most 400 KB each,
with seed-1 data only. Scroll the subject into view first. The README and the
equivalence report use them from `docs/img/after/`.

## The walkthrough

`test_walkthrough.py`, marked `walkthrough`, runs the first `scope.must_keep`
workflow from sign-in to its last step inside `record_context("walkthrough")`, with
`pause(page)` between steps. Reuse the workflow module's step function rather than
writing the steps twice. Import it from the module and name it without the `test_`
prefix, so pytest does not collect it twice.

## The two acceptance runs

```sh
# Screenshots into the tracked docs folder (every test):
rm -rf <engagement root>/docs/img/after && mkdir -p <engagement root>/docs/img/after
cd <engagement root>/app && E2E_SCREENSHOT_DIR=<engagement root>/docs/img/after \
  uv run pytest -q --create-db tests/e2e

# The walkthrough video into the private design zone (never committed):
mkdir -p <private>/synthetic/media/walkthrough
cd <engagement root>/app && E2E_VIDEO_DIR=<private>/synthetic/media/walkthrough \
  uv run pytest -q --create-db -m walkthrough tests/e2e
```

Target CI (the e2e job from S4.template) runs `uv run pytest -q --create-db tests/e2e`
on seed 1 with neither variable set. The tests must pass there and write nothing:
no screenshot, no video and no file outside the target repo.

## Before S5.e2e

Things the browser tests have caught, which the foundation and the feature areas can
check first:

- After signing in, users must land where the rules say. `LOGIN_REDIRECT_URL`
  decides it. A URL the user must open by hand does not count.
- A list whose table container has `hx-boost="true"` with an inherited `hx-target`
  swaps a row link's whole page into the table. Give the row links `hx-boost="false"`,
  or boost only the paging and sorting links.
- A double-click handler bound to a radio input does not fire when the user
  double-clicks the label next to it. Bind it to the option's container.
