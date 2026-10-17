# Contracts: formats, CLIs, placeholders and paths

**Status:** v2, 2026-10-16, after the integration round. Nothing here has been built yet.
**What this is:** every artifact format, CLI contract, placeholder and path rule that tasks in different stages and sessions must agree on. When a task's text in `pipeline.json` and this file disagree, this file wins; fix the task. `pipeline.json` `inputs` cite sections as `docs/contracts.md#<anchor>` (for example `docs/contracts.md#rules-json`). The plan is docs/proposal.md; the orchestrator's procedure is docs/runbook.md.

`<eng>` below always means `{{p.records}}/engagement.json`.

<a id="conventions"></a>
## Conventions

| Topic | Rule |
|---|---|
| Exit codes | `0` pass. `1` a check failed (mismatch, threshold, lint, guard hit). `2` usage error, missing input, missing environment (file, env var, jar, `JAVA_HOME`), an engagement that does not load (§2), or a refused data-plane rule (§3). `5` `equivalence` selected nothing in any cell and `--allow-empty` was not given. Any other code counts as `2`. |
| Versions | Every artifact whose example below has `format_version` carries `"format_version": 1`; canonical output carries `canonical_version 1`. A reader exits 2 on a version it does not know. |
| Encoding | UTF-8 without BOM. LF line ends, except CSV (CRLF, RFC 4180). |
| JSON written by tools | `json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True)` plus a final newline, so `--check` compares bytes. |
| Dates | `YYYY-MM-DD`. File-name timestamps `YYYYMMDDTHHMMSSZ` (UTC). Tools never write the current time into committed files, except the `date` and `recorded` fields named here. |
| `--check` | Regenerate in memory or into a temp dir, compare, exit 1 on any difference (a missing output counts), write nothing. |
| No values | Committed files, prompts, summaries, notes and answers hold names, counts and hashes, never data values (cells, rows, parameter values). |
| Examples | Object names, counts and hashes in examples are invented. The only measured facts are the Northwind and InvoiceGenerator counts and pins in §5 and §6 and the sample-output facts in §16. `$HOME` in an example stands for the operator's expanded home directory; real files and commands always hold the expanded absolute path. Repos sit under `$HOME/sourcecode/` (`modernization-pipeline`, `access-excel-to-django`). |
| File-safe id | A unit or table name used as a file name: each character outside `[A-Za-z0-9._-]` becomes `_`; if anything was replaced, append `-` and the first 8 hex digits of sha256(name in UTF-8). `qryOrderTotals` stays; `Order Details` becomes `Order_Details-fe3ac0d4`; `InvoiceTable[Net Amount]` becomes `InvoiceTable_Net_Amount_-f34d32b9`. Used by dataset table files (§7), S2.reference (`out/reference/sql/`, §9) and S2.golden (golden files, §11). |

---

<a id="placeholders-and-paths"></a>
## 1. placeholders-and-paths

<a id="placeholders"></a>
### Placeholders
The driver fills them in every `pipeline.json` string before an agent sees it. Agents never fill them.

| Placeholder | Value |
|---|---|
| `{{e.<field>}}` | Any engagement field by dotted path, after `{{p.*}}` substitution (§2). A string, number or boolean renders as text; an array of scalars renders as its items joined with `; ` (`none` when empty), as the driver's `render()` does. A null or missing field, or an object, stays unresolved, which is a driver problem. |
| `{{e.db_name}}` | `engagement.db_name`, or the id with every character outside `[a-z0-9]` replaced by `_`. Must match `^[a-z][a-z0-9_]{1,40}$`. |
| `{{p.pipeline}}` `{{p.target_repo}}` `{{p.target}}` `{{p.private}}` `{{p.records}}` `{{p.env_file}}` | The six paths of `paths.local.json` (below). |
| `{{s.id}}` `{{s.slug}}` `{{s.title}}` | The stage. |
| `{{s.change_order}}` | `CO-NNN` when `args.change_order` is set; otherwise unresolved. |
| `{{item.id}}` `{{item.title}}` `{{item.scope}}` | The fan-out item. Only inside a fan-out task's `each`. `scope` is a string. |
| `$MP_DB_SUFFIX` | Not a placeholder: a shell variable that every command form sets to the agent's slot (Command forms below). In a data-plane call the driver replaces it with the literal slot, inside double quotes too. |
| `"$PG_URL"`, `"$MSSQL_URL"`, `"$JAVA_HOME"` … | Shell variables from the env file, expanded at run time. Not placeholders. Never inside a data-plane call. |

An unresolved `{{...}}` in a filled task, exit criterion, gate or prepare command is a driver problem. Raw `each` templates are checked per item after filling.

<a id="fixed-locations"></a>
### Fixed locations

| What | Path |
|---|---|
| Engagement dir | `$HOME/engagements/<id>/` |
| `{{p.private}}` | `$HOME/engagements/<id>/private`. Same path in every stage and session; the stage `prepare` list rebuilds lost contents. |
| `{{p.records}}` | Demos: `<pipeline>/engagements/<id>` (default when omitted). Clients: `$HOME/engagements/<id>/record`, its own local git repo (`git init` before S0), never in the pipeline repo or a cloud session. |
| `{{p.env_file}}` | `$HOME/.config/modernization/env` (agent credentials and tool paths) |
| data.env | `$HOME/.config/modernization/data.env` (secrets; only mp-data and the operator read it) |
| mp-data, mp-run, mp-bash-hook | `$HOME/.local/bin/` (mode-755 copies); reviewed toolkit releases in `$HOME/.local/share/modernization/toolkit@<sha>` (each with its own `.venv`), the current one named in `$HOME/.local/share/modernization/toolkit.pin`, run with `UV_CACHE_DIR=$HOME/.local/share/modernization/uv-cache` (§3) |
| User-space tools | `$HOME/.local/share/modernization/{uv-venv,jdk-21,syslibs}`, `$HOME/.local/bin/{uv,uvx,jq}`, browsers in `$HOME/.cache/ms-playwright` |
| Client-session settings | `$HOME/engagements/<id>/client-session.json`, generated per engagement (§17) |
| Client-host marker | `$HOME/.config/modernization/client-host` (exists only on approved machines) |

Session start (docs/runbook.md):
- Demos: `. $HOME/.config/modernization/env && cd <pipeline> && claude --permission-mode auto --add-dir <target_repo> --add-dir $HOME/engagements/<id>` (without auto mode: `--allowedTools 'Bash(mp-run:*)' 'Bash(mp-data:*)'` instead; docs/runbook.md 0.8).
- Clients: `. $HOME/.config/modernization/env && cd <pipeline worktree> && claude --settings $HOME/engagements/<id>/client-session.json --add-dir <target_repo> --add-dir $HOME/engagements/<id>` (§17).
- Sourcing the env file first puts `$HOME/.local/bin` on PATH, so `mp-data` and `mp-run` resolve as bare words in emitted commands.

<a id="private-zones"></a>
### Private directory zones

| Path under `{{p.private}}` | Zone | Written by |
|---|---|---|
| `out/model/` | design | S0.parse (§4) |
| `out/inventory.json` | design | S0.healthcheck (§5) |
| `out/estimate.json` | design | S0.healthcheck (`toolkit estimate`, §15); the health check's bands and the Stage 1 price when Stage 1 is sold alone |
| `out/reference/catalog.json`, `out/reference/sql/` | design | `uv run toolkit reference sql` (S2.reference, S2.reference_xlsm, §9) |
| `review/<slot>/` | design | reviewers: quiet summaries, regenerated design files and synthetic outputs only |
| `synthetic/reference/seed-N.accdb` (xlsm `.xlsx`) | design | `uv run toolkit reference build <eng> --dataset seeds\|seed-N` (§9) |
| `synthetic/results/<stage>/` | design | synthetic-only equivalence JUnit and matrices; app fixture JUnit `junit-app-fixtures-<db>.xml` (§12) |
| `synthetic/results/obtained/` | design | pytest-regressions obtained files (§12) |
| `synthetic/media/` | design | S5.e2e screenshots and video on seed data; the walkthrough video in `synthetic/media/walkthrough/` |
| `sources/` | data | fetch (S0.workspace) |
| `datasets/` | data | dataset export (S0.dataset, §7) |
| `workbook/` | data | parse, xlsm cached values (S0.parse) |
| `reference/` | data | `mp-data reference build <eng> --dataset engagement`: only `reference/engagement.accdb` (xlsm `.xlsx`) |
| `goldens/` | data | golden record: engagement goldens and parameter values (S2.golden, §11) |
| `corpus/` | data | guard (S0.guard) |
| `results/<stage>/` | data | data-plane equivalence JUnit and matrices (engagement cells and the seed cells of the same run, §12) |
| `logs/` | data | mp-data logs, harness redaction, and the per-call `TMPDIR` of each data-plane call (`logs/tmp-*`, removed afterwards; §3, §12) |
| `assets/` | data | you: client-supplied screenshots (engagement `assets.screenshots`) |
| `scrub-map.json` | data | parse (S0.parse) |

- Agents may read the design zone. Every read of a data-zone path is a data-plane call (§3); agents never open, list, cat, query or copy data-zone paths, and synthetic outputs never go there.
- Commands that touch only the design zone and committed files run as `uv run toolkit ...` with full output; debugging happens there, on synthetic seeds.
- `pipeline.json` `data_plane.zones` lists exactly these paths (design: `out/`, `review/`, `synthetic/`; data: the rest).
- Every mp-run command (§17) sees the data-zone directories as empty, and `scrub-map.json` and data.env as unreadable (`Permission denied`). Client sessions also deny the data zone to the Read, Edit and Write tools (§17).
- mp-data's toolkit child runs in a jail of its own (§3): it may write only this engagement's data zone, `$TMPDIR` and the call's declared outputs. So a data-plane call never writes a design-zone file unless the design-zone path is a declared output (for example `parse --out {{p.private}}/out/model`).

<a id="threat-model"></a>
**Threat model.** The zones and the means that enforce them (mp-run and mp-data's jail, the client Bash hook and deny rules, the database roles, quiet tools and guard) stop *accidental* exposure of client data, to the model context, to committed files and to databases agents can read, by agents that follow their prompts. They use cheap operating-system means and do not claim to contain a deliberately hostile agent; v2 adds no further hardening. These residual risks are recorded, not fixed (docs/proposal.md §11, row "hostile-agent residuals"):
- home dotfiles and git and gh config that stay writable inside mp-run and that your plain terminal reads later (`~/.bashrc`, `~/.profile`, `~/.gitconfig`, `~/.config/git`, `~/.config/gh`, `~/.ssh`, `~/.config/systemd/user`);
- agent-written app code that you run in a plain terminal at client gates (the S5 import rehearsal and the staging load);
- the pre-commit and uv caches that plain-terminal hooks and the `hooks` and `chromium` environment fixes use;
- the records repo's git hooks and config;
- network egress from mp-run.

A review finding against the isolation is a stated mechanism that does not work, not a missing bind, mask or deny rule.

<a id="paths-local-json"></a>
### paths.local.json
- **Path:** `{{p.records}}/paths.local.json`, gitignored (pipeline `.gitignore`: `engagements/*/paths.local.json`; a client records repo ignores `paths.local.json`).
- **Producer:** the orchestrator, before every run (docs/runbook.md). The same values go into the driver's `args.paths`.
- **Consumers:** `toolkit.engagement.load` (§2), so every toolkit command that takes `<eng>`; mp-data; mp-run.

```json
{
  "format_version": 1,
  "pipeline": "$HOME/sourcecode/modernization-pipeline",
  "target_repo": "$HOME/sourcecode/access-excel-to-django",
  "target": "$HOME/sourcecode/access-excel-to-django/northwind",
  "private": "$HOME/engagements/northwind-demo/private",
  "records": "$HOME/sourcecode/modernization-pipeline/engagements/northwind-demo",
  "env_file": "$HOME/.config/modernization/env"
}
```
- All six keys are required: absolute, expanded (no `~`, no `$`; the example writes `$HOME` only as the Conventions say), no trailing `/`, no whitespace (commands use them unquoted).
- `target` = `target_repo` when `target.root` is `.`, else `target_repo/<target.root>`.
- Missing file or key: every command that takes `<eng>` exits 2 naming it.

<a id="env-file"></a>
### Env file and data.env

**Env file.** Path `{{p.env_file}}`, chmod 600. Producers: the environment items in `pipeline.json` `environment` (`env_file`, `jdk`, `syslibs`, `postgres`, `mssql`, `chromium`). Consumers: every command form (mp-data and mp-run source it themselves; the braced form sources it first).
```
export PATH=$HOME/.local/bin:$PATH
export UV_PYTHON_PREFERENCE=only-managed
export JAVA_HOME=$HOME/.local/share/modernization/jdk-21
export LD_LIBRARY_PATH=$HOME/.local/share/modernization/syslibs/usr/lib/x86_64-linux-gnu
export PG_URL=postgresql://mp_agent:mp-agent@127.0.0.1:55432
export MSSQL_URL=mssql://mp_agent:<generated once>@127.0.0.1:14330
export PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright
```
- Only `export NAME=value` lines; values absolute.
- It holds the agent role only. `mp_agent` cannot open any `mpdata_*` database (Databases below), so nothing in this file reaches engagement rows. No secret value goes here; an `MP_MSSQL_SA_PASSWORD` line left by an older fix is moved to data.env by the `mssql` item.
- The toolkit maps `postgresql://` to SQLAlchemy `postgresql+psycopg` and `mssql://` to `mssql+mssqlpython`. Django's django-environ reads both as they are (`mssql://` gives ENGINE `mssql`); the overlay sets OPTIONS `python_driver` = `mssql_python` (§14).

**data.env.** Path `$HOME/.config/modernization/data.env`, chmod 600, only `export NAME=value` lines (the `env_file` check rejects any other line). Producers: the `env_file` environment item creates it; the `postgres` and `mssql` items write their lines (run by the operator; for clients in a plain terminal); you add client secrets named by `env:` refs (§2). Consumers: mp-data (it reads this file after the env file, outside its jail, §3), the environment checks, and gate steps you run in a plain terminal. mp-run hides it, mp-data's toolkit child never sees it, and client sessions deny it (§17).
```
export MP_DATA_PG_URL=postgresql://postgres:<generated hex>@127.0.0.1:55432
export MP_RUNNER_PG_URL=postgresql://mp_runner:<generated hex>@127.0.0.1:55432
export MP_MSSQL_SA_PASSWORD=<generated once>
export MP_DATA_MSSQL_URL=mssql://sa:<the same password>@127.0.0.1:14330
export MP_RUNNER_MSSQL_URL=mssql://mp_runner:<generated once>@127.0.0.1:14330
export GITHUB_PUSH_C2026001=<token, only when secrets.github_push.ref is env:GITHUB_PUSH_C2026001>
```
- Generated passwords: PostgreSQL `postgres` and `mp_runner` take `openssl rand -hex 24`; every SQL Server password (`sa`, `mp_agent`, `mp_runner`) is `$(openssl rand -hex 16)Aa1-`, which is URL-safe and meets the SQL Server password policy. Each is generated once by its environment item and never printed.
- `MP_DATA_*` and `MP_RUNNER_*` exist only in this file. Inside mp-data the toolkit child sees one of them under two names (§3); outside mp-data no process gets them.
- Values are literal: mp-data and `toolkit.secrets` take the text after `=` as it is (no quotes, no expansion), and plain-terminal gate steps source the file. So a value holds only `A-Z`, `a-z`, `0-9` and `_ . : / @ % + = , -`; a secret with any other character (for example a URL with `?` or `&` in its query) goes in `pass` (docs/runbook.md 1.6).

<a id="command-forms"></a>
### Command forms
`pipeline.json` commands are written without `cd {{p.pipeline}} &&`, without sourcing the env file and without `mp-run`. The driver turns each acceptance, verify, exit and prepare command into one of three forms. The same forms apply to the prepare list and exit criteria the dry run returns, and to the driver's own integrate and checkpoint checks; the orchestrator runs them exactly as returned.

| Form | Used for | Emitted as |
|---|---|---|
| 1. Data-plane call | every command that is a data-plane call (§3) | `mp-data --slot <slot> <rest>`, with every `$MP_DB_SUFFIX` replaced by the literal slot. No wrapper: mp-data sources the env files and changes directory itself. |
| 2. mp-run | every other command, except in S0 bootstrap runs | `mp-run --eng {{p.records}}/engagement.json --slot <slot> -- '<filled command>'`. A single quote inside the command is written `'\''`. mp-run sources the env file, exports `MP_DB_SUFFIX`, changes to the pipeline unless the command starts with `cd `, and hides the data zone (§17). |
| 3. Braced | non-data-plane commands when S0 runs in bootstrap mode (mp-run does not exist yet) | `. <env_file> && export MP_DB_SUFFIX=<slot> && cd <pipeline> && { <cmd>; }`. When `<cmd>` starts with `cd `, the `cd <pipeline> &&` part is left out. The braces stop `cmd; test $? -eq 1` from passing when the env file or the `cd` fails. |

```
mp-data --slot s5_review_r1 equivalence $HOME/sourcecode/modernization-pipeline/engagements/northwind-demo/engagement.json --adapter django --datasets all --dbs from-engagement -k '[orders::' --only-areas orders --min-pass-per-cell 0.9 --allow-empty
mp-run --eng $HOME/sourcecode/modernization-pipeline/engagements/northwind-demo/engagement.json --slot s5_feature_orders -- 'cd $HOME/sourcecode/access-excel-to-django/northwind/app && MP_ONLY_AREAS=orders uv run pytest -q --create-db northwind/orders'
. $HOME/.config/modernization/env && export MP_DB_SUFFIX=s0_workspace && cd $HOME/sourcecode/modernization-pipeline && { uv run pytest -q toolkit/tests/test_fetch.py; }
```
(`$HOME` stands for the expanded path, as everywhere in this file; the real lines carry none.)

Rules:
- Environment `check`, `fix` and `when` commands are never wrapped: they need Docker and data.env. The dry run returns them filled but unanchored; the orchestrator runs them directly in demo sessions and in a plain terminal for clients.
- Agent commands (acceptance, verify, exit, prepare) are one line. A command with a newline or carriage return is a driver problem: `<where>: command spans more than one line (the client Bash hook blocks newlines)`. Environment `check`, `fix` and `when` commands are exempt.
- A command that invokes mp-data as a command word (at the start or behind `NAME=value` assignments or `env`, or after `&&`, `||`, `;`, `|`, `&`, `then`, `do`, `else`, `(`, `{`, `$(` or a backtick) but is not a data-plane call (§3) is a driver problem: `<where>: mp-data must be a standalone data-plane call`. So `X=1 mp-data ...` and `env mp-data ...` are problems too. This rule covers environment `check`, `fix` and `when` commands as well (the driver checks every `pipeline.json` command; the `mp_data` check reads `toolkit.pin` directly instead of calling `mp-data pin --status`). Mentions of mp-data as text are fine (grep patterns, jq strings, `command -v mp-data`, `cmp -s scripts/mp-data ...`).
- A data-plane call in `pipeline.json` never passes `--slot`; the driver adds it (a driver problem otherwise).
- Acceptance, verify, exit and prepare lists run in order and each entry must exit 0. A data-plane step and a check on its output are two entries (`A`, `B`), never `A && B`.
- mp-data creates the parent directory of every `--out`, `--report` and `--matrix` path and the `--junit-dir` directory itself, so no `mkdir -p` comes before it.
- `<slot>`: the agent label lower-cased, each run of characters outside `[a-z0-9]` replaced by `_`, leading and trailing `_` dropped (`S5.feature.orders` gives `s5_feature_orders`, `S5.review:r1` gives `s5_review_r1`). A slot longer than 24 characters becomes its first 17 characters, `_` and a 6-character hash of the whole slot. The prepare list and `items_from` commands use the slot of `<stage>:prepare` (`s0_prepare`, `s1_prepare`); exit criteria and integrate checks use `<stage>:integrate` (`s1_integrate`); the checkpoint agent uses `<stage>:checkpoint` (`s1_checkpoint`).
- Prompts show the emitted strings, and acceptance checking compares reports against them verbatim (whitespace runs normalised).
- App commands start with `cd {{p.target}}/app && ...`.
- Engagement data: a data-plane call (§3). Design-zone and synthetic work: `uv run toolkit <cmd> ...`.
- No command starts with `!`. Write `cmd; test $? -eq 1` or a positive check.
- Database URLs in non-data-plane commands are `"$PG_URL/<name>"` and `"$MSSQL_URL/<name>"`. A data-plane call names a local database as `local:<name>` (stage1) and never carries a URL. No Unix-socket URLs anywhere.

<a id="dry-run"></a>
**What the dry run returns** (the orchestrator runs these exactly as returned, docs/runbook.md):
- `preflight`: the ids of the environment items whose `stages` include the stage and whose `kinds` (if any) include the source kind, in `pipeline.json` `environment` order. An item with a `when` is listed without evaluating it; it applies only when its `when` exits 0, which the orchestrator checks first. Stages have no `preflight` lists.
- `environment`: those items, filled with the engagement's paths and never wrapped; `when` is filled but not evaluated (the orchestrator runs it first and skips the item when it exits non-zero).
- `prepare` and `exit_criteria`: the emitted forms above.
- `items_from.<fan-out id>`: `{file, jq, cmd}`, where `cmd` is `jq -c '<jq>' <file>` in the mp-run form with the prepare slot (the client Bash hook would block the bare jq call). Its output is `args.items["<fan-out id>"]` (§8).
- Driver problems on the shared lists: a duplicate environment id, an item without `stages`, an unknown stage or an invalid kind in an item, an empty `check`, a stage that still has `preflight` (`<stage>: remove stage preflight; environment[].stages is the source`), a stage `offer` field, and a stage `offers` entry that is not a key of `pipeline.json` `offers`.

<a id="launcher"></a>
**Launcher.** Workflow scripts cannot read files, so the driver gets every file in its args. The Workflow tool refuses a script file over 524288 bytes, and `pipeline.json` alone is about 500 KB as compact JSON (about 540 KB as the indented file), so it can be neither typed inline nor embedded whole. The orchestrator therefore writes a per-stage launcher and calls that:
```
python3 -I {{p.pipeline}}/.claude/workflows/make-launcher.py --pipeline {{p.pipeline}} --records {{p.records}} --stage <S> --args $HOME/engagements/<id>/args-<S>.json --out $HOME/engagements/<id>/launch-<S>.workflow.js [--prior]
Workflow({ scriptPath: "$HOME/engagements/<id>/launch-<S>.workflow.js" })
```
- `make-launcher.py` (standard library only) sits next to `run-stage.js` in `{{p.pipeline}}/.claude/workflows/` (setup step 0 copies both). In client sessions the orchestrator runs it through mp-run (§17); `{{p.records}}` and `$HOME/engagements/<id>/` are writable there.
- The args file, written by the orchestrator, holds the driver args that are not files: `paths`, `dry_run` or `prepared`, `attempt`, `rerun`, `since`, `items`, `change_order`, `host_attested`, `commit_trailer`, `max_agents`, `allow_over_budget`, `checkpoint`. It never holds `pipeline`, `engagement`, `gates`, `prior` or `prior_items` (exit 2).
- The launcher embeds `pipeline.json` sliced, `engagement.json`, `gates.json` when present and, with `--prior`, the `results` and `items` of `runs/<S>.json` (a change-order run: `runs/S1-CO-NNN.json`) as `prior` and `prior_items`, and calls `run-stage.js` with `workflow({scriptPath})`.
- Slice rule: the top-level keys and the stage being run in full; every other stage carries only `id`, `slug`, `title`, `applies_to`, `offers`, `offer_note` and `bootstrap_only`, which is all the driver reads from them. So the dry run of the slice equals that of the full file (checked for both demos and all seven stages).
- `make-launcher.py` refuses (exit 2) a launcher over 500000 bytes, a margin under the 524288-byte Workflow script cap.
- Regenerate the launcher after any change to an embedded file or the args (gates.json, a new runs file, a new args file).

<a id="databases"></a>
### Databases

| Name | Holds | Created by | Role that connects |
|---|---|---|---|
| `mpdata_{{e.db_name}}_stage1` | Stage 1 load of the engagement data | `mp-data stage1 <eng> --target-db local:mpdata_{{e.db_name}}_stage1 ...` | superuser (`postgres`, `sa`), through mp-data only |
| `mpdata_{{e.db_name}}_gate` | engagement data imported at the S5 client gate (import rehearsal) | you, in a plain terminal: the superuser creates it owned by `mp_runner` (PostgreSQL `docker exec mp-pg16 createdb -U postgres -O mp_runner ...`; SQL Server `CREATE DATABASE` then `ALTER AUTHORIZATION ON DATABASE::... TO mp_runner`, as `sa`) and drops it once the counts match | `mp_runner`: the app gets only `MP_RUNNER_PG_URL` or `MP_RUNNER_MSSQL_URL`, read from data.env in a subshell |
| `mpdata_test_{{e.db_name}}_<slot>` | Django test databases of data-plane equivalence runs | `mp-data equivalence ... --adapter django` (mp-data sets `MP_TEST_DB_PREFIX=mpdata_test_`) | `mp_runner`, through mp-data only |
| `{{e.db_name}}_dev` | schema only (S3.move, S4 inspectdb) | `uv run toolkit stage1 <eng> --target-db "$PG_URL/{{e.db_name}}_dev" --schema-only ...` | `mp_agent` |
| `{{e.db_name}}` | app dev database (`app/.env`), synthetic data only | the app; `scripts/dev_db.sh createdb {{e.db_name}}` at gates | `mp_agent` |
| `test_{{e.db_name}}_<slot>` | Django test databases of agent runs, synthetic data only | pytest-django (§14) | `mp_agent` |

The test database name is `<MP_TEST_DB_PREFIX>{{e.db_name}}_<MP_DB_SUFFIX>`: prefix `test_` by default, `mpdata_test_` in data-plane runs (§14).

Servers: Docker containers `mp-pg16` (127.0.0.1:55432) and `mp-mssql22` (127.0.0.1:14330). Only the `postgres` and `mssql` environment items start and provision them (containers, roles, passwords, `pg_hba.conf`); `scripts/dev_db.sh` only creates and drops agent databases (§18). Docker is not reachable inside mp-run.

Roles (three per server):
- **PostgreSQL** (item `postgres`): superuser `postgres` with a generated hex password, kept only in data.env (`MP_DATA_PG_URL`). Role `mp_agent` LOGIN CREATEDB, password `mp-agent`, in `PG_URL` (env file). Role `mp_runner` LOGIN CREATEDB with a generated hex password, only in data.env (`MP_RUNNER_PG_URL`). The item keeps these five lines at the top of `pg_hba.conf`, in this order, then runs `SELECT pg_reload_conf()`:
  ```
  host /^mpdata_ mp_agent all reject
  host postgres,/^mpdata_ mp_runner all scram-sha-256
  host all mp_runner all reject
  host all all 127.0.0.1/32 scram-sha-256
  host all all ::1/128 scram-sha-256
  ```
  PostgreSQL 16 reads a database entry that starts with `/` as a regular expression. So `mp_agent` never reaches an `mpdata_` database, and `mp_runner` reaches only those and the `postgres` maintenance database. The last two lines replace the trust that initdb's defaults give inside the container. The item's check proves that these lines lead the file, that `mp_agent` is rejected from a database named `mpdata_probe` (the superuser creates it when missing), and that `mp_runner` reaches `mpdata_probe` but is rejected from `template1`.
- **SQL Server** (item `mssql`): the `sa` password only in data.env (`MP_MSSQL_SA_PASSWORD`, `MP_DATA_MSSQL_URL`). Logins `mp_agent` (`MSSQL_URL`, env file) and `mp_runner` (`MP_RUNNER_MSSQL_URL`, data.env only), each with its own generated password and `GRANT CREATE ANY DATABASE` (not `dbcreator`). A new database maps only its creator (as dbo), so `mp_agent` cannot open the databases that `sa` or `mp_runner` create; the check proves it on `mpdata_probe`.

Rules:
- Engagement rows only ever enter local databases whose name starts with `mpdata_`, or a client `secret:` target (§18 stage1). mp-data refuses (exit 2) to load engagement data into a local database whose name lacks the prefix; `stage1` and `import_dataset` (§14) refuse it too.
- No engagement rows enter an `mpdata_` database owned by anyone but `postgres`, `sa` or `mp_runner` (PostgreSQL `pg_database.datdba`, SQL Server `sys.databases.owner_sid`). Before `stage1`, `equivalence`, `reference build|run|selfcheck` or `golden record` runs, mp-data exits 2, running nothing, when any `mpdata_*` database on a server the command reaches has another owner (§3 step 7), and stage1 checks its own target the same way (§18), as does `import_dataset` (§14) before it loads an engagement dataset into a local database. Reason: `mp_agent` has CREATEDB (CREATE ANY DATABASE), so it could create an `mpdata_` database and own it; after a load it could read the rows as dbo on SQL Server, or rename the database past the reject line on PostgreSQL. Drop such a database as the superuser.
- Which URL a data-plane command gets (§3): commands that run agent-written code (`equivalence`, `reference build|run|selfcheck`, `golden record`) get the `mp_runner` URLs; toolkit-only commands (`stage1`, `purge`) get the superuser URLs. Inside mp-data the toolkit child sees the chosen URL under both names, `MP_DATA_PG_URL` and `PG_URL` (likewise `MP_DATA_MSSQL_URL` and `MSSQL_URL`). Outside mp-data, `PG_URL` and `MSSQL_URL` are the `mp_agent` URLs and `MP_DATA_*` and `MP_RUNNER_*` exist only in data.env.
- Gate steps that open or load engagement data use `mpdata_` names and run in a plain terminal: the S3 relink rehearsal connects the Access front end to `mpdata_{{e.db_name}}_stage1` with the superuser login from data.env; the S5 client gate loads `mpdata_{{e.db_name}}_gate` through the agent-written app, which gets only the `mp_runner` URL (data.env is never sourced into its shell). That app code still runs outside any jail, a recorded residual risk (§1 Threat model).
- `purge` drops `mpdata_{{e.db_name}}_*`, `mpdata_test_{{e.db_name}}_*`, `{{e.db_name}}_dev`, `{{e.db_name}}` and `test_{{e.db_name}}_*` on every running local dialect (§15).

<a id="owned-paths"></a>
### Owned paths

| Prefix | Means | Integrate stages it in |
|---|---|---|
| `pipeline:<p>` | `{{p.pipeline}}/<p>` | pipeline repo |
| `target:<p>` | `{{p.target}}/<p>` | target repo |
| `target-repo:<p>` | `{{p.target_repo}}/<p>` | target repo |
| `records:<p>` | `{{p.records}}/<p>` | records repo (for demos, the pipeline repo) |
| `private:<p>` | `{{p.private}}/<p>` | never staged |

- A trailing `/` means the directory and everything under it. `*` matches inside one path segment (`test_parse_*.py`, `seed-*/input/`).
- `owns_except` removes paths from `owns`; prompts show them as "never edit".
- When `target.root` is `.`, `target:` and `target-repo:` name the same root for overlap checks.
- Two tasks that can run at the same time must not own overlapping paths (driver problem). No task owns a gitignored path.

<a id="demo-repo-root-files"></a>
### Demo repo root files
The demo target repo holds several engagements. Its root `README.md` and `NOTICE.md` keep one section per engagement between the marker lines `<!-- engagement:<id> -->` and `<!-- /engagement:<id> -->`, each on a line of its own. Setup step 0 (docs/runbook.md) creates both files; each engagement's S6.handover replaces only the text between its own markers.

<a id="identifiers"></a>
### Identifiers

| Id | Rule |
|---|---|
| Engagement id | `^[a-z][a-z0-9]*(-[a-z0-9]+)*$`, at most 40 characters. Clients get an opaque id `c<YYYY><NNN>` (`c2026001`); the client's name appears only in the records. |
| `app.slug` | `^[a-z][a-z0-9_]{1,29}$`, not in `pipeline.json` `slug_reserved` |
| `app.landing` | Optional URL name `<namespace>:<name>` (`^[a-z_][a-z0-9_]*:[A-Za-z0-9_]+$`) a user lands on after sign-in; `toolkit new-app` renders it as `LOGIN_REDIRECT_URL` (default `users:redirect`). Set at S5 once the landing screen exists, then regenerate the app config with `new-app`. |
| Area and fan-out item ids | `pipeline.json` `item_ids.pattern` `^[a-z][a-z0-9_]{1,29}$`, not a Python keyword, not in `item_ids.reserved`, not the app slug, unique. An S1 area id is the S5 item id, the Django app label and the manifest `area`. |
| Slot | `^[a-z0-9_]{1,24}$`, from the agent label (Command forms). mp-data and mp-run exit 2 on any other `--slot`. |
| Offer key | A key of `pipeline.json` `offers` (`health-check`, `assessment`, `stage1`, `stage2-small`, `stage2-standard`, `care-plan`). A stage's `offers` lists keys (S0 `health-check`; S1 `assessment`; S3 `stage1`; S2, S4, S5, S6 `stage2-small`, `stage2-standard`), with free text in `offer_note`; engagement `commercial.purchased` lists keys. An unknown key is a driver problem. |
| Rule / question | `R-<area>-NNN`, `Q-<area>-NNN` (3 digits). Never reused or renumbered. |
| Change order | `CO-NNN` |
| Unit | Access: the query name. xlsm: `<Table>[<Column>]` (calculated column), `<Sheet>!<A1 range>` (formula block), `invoice_pdf`, `invoice_xlsx`, `email_draft` (documents). |

---

<a id="engagement-loading"></a>
## 2. engagement-loading

- **Path:** `{{p.records}}/engagement.json`, with the sibling `paths.local.json` and the optional `source.lock.json` (§6) and `inventory.lock.json` (§5).
- **Producer:** you. Demos are committed in `engagements/<id>/`; clients copy `engagements/_template/engagement.json` into their records repo.
- **Loader:** `toolkit.engagement.load(path)` in `pipeline:toolkit/engagement.py` (S0.workspace). `toolkit engagement-lint <eng>` (jsonschema `toolkit/schemas/engagement.schema.json`) and the driver apply the same rules.
- **Consumers:** every toolkit command that takes `<eng>`, mp-data, mp-run, the driver (`args.engagement` + `args.paths`).

```json
{
  "id": "northwind-demo",
  "db_name": "northwind_demo",
  "bootstrap": true,
  "source": {
    "kind": "accdt",
    "path": "{{p.private}}/sources/northwind.accdt",
    "bytes": 12895205,
    "sha256": "5c38b53e1600c2e21ddd91cb17ce6719615c6a32deff5441fffbcb239df6e5e2",
    "data_copy": null,
    "saved_with_data": true
  },
  "target": {"repo": "thomas-sodon/access-excel-to-django", "root": "northwind", "mode": "github"},
  "app": {"slug": "northwind", "project_name": "Northwind", "timezone": "UTC", "use_tz": false},
  "database": {"primary": "postgresql", "verify_also": ["mssql"]},
  "data_policy": {"execution": "cloud-allowed"},
  "_note": "keys starting with _ are never substituted or validated"
}
```

`load()` does, in order:
1. Read engagement.json. Its directory is `{{p.records}}`.
2. Read the sibling `paths.local.json` (§1).
3. Replace `{{p.<key>}}` in every string value whose key does not start with `_`, using regex `\{\{([\w.]+)\}\}` and the `paths.local.json` keys. `source.path` above becomes `$HOME/engagements/northwind-demo/private/sources/northwind.accdt` (expanded).
4. Derive `db_name` (§1).
5. Fill pins from `source.lock.json` where engagement.json has null or nothing: `source.bytes`, `source.sha256`, `source.commit`, `source.data_copy_sha256`, `source.backends[].sha256`. Engagement values win. Load `inventory.lock.json` as `inventory_lock` for `inventory --expect` (§5); `expected_inventory` is never rewritten.
6. Validate, at every depth, skipping keys that start with `_`:
   - no string value may still contain `{{` or a template marker matching `<[^<>]{1,80}>`;
   - `source.kind` is in `pipeline.json` `source_kinds` (`accdt`, `vcs-export`, `accdb`, `mdb`, `xlsm`); id, `app.slug` and `db_name` follow §1;
   - `data_policy.execution` is `local` (clients; the driver requires it when `bootstrap` is false) or `cloud-allowed` (demos);
   - when `records` is set (clients), it equals `paths.local.json` `records`;
   - every secret ref (`secrets.github_push.ref`, `accdb_password_ref`, `stage1_target_url_ref`, `staging_db_url_ref`, `production_db_url_ref`, `entra.client_secret_ref`, and `source.backends[].password_ref`) is null or matches `^(env:[A-Z_][A-Z0-9_]*|pass:\S+)$`;
   - `source.external_links[]` items are `{name, type}` with `type` `odbc-sqlserver`, `sharepoint`, `excel` or `text`;
   - `target.repo` is the GitHub `owner/name` (null when `target.mode` is `local`).

Optional keys in `engagement.schema.json`:
- Demo-only: `source.url`, `git`, `branch`, `commit`, `clone_dir`, `vba_export`, `sample_outputs`, `on_mismatch`, `content_md5`, `etag`, `last_modified`, and `scope.email`, `scope.dashboard`.
- `source.licence`, carried by the template and both demos.
- `database.stage1`, `secrets.github_push` and `secrets.entra` may be null (both demos carry null).

One data back end: `source.data_copy` names the single file that holds the data. `source.backends` lists the linked back ends so parse can resolve links. A split app with more than one data back end is not supported: the health check flags it as `custom quote: more than one data back end`, `dataset export` exits 2, and `parse --fail-on multi-backend` exits 2.

A back end that is listed but not received is not an error for S0 and S1 (the free health check needs only the design file): parse models what the front end holds of the linked tables, the health check flags "back end not received", and `dataset export` reports status no-data for their tables. The S2 and S3 prepare lists run parse with `--fail-on missing-backend`.

Fields tools read:

| Field | Read by |
|---|---|
| `source.kind`, `path`, `url`, `bytes`, `sha256`, `last_modified`, `etag`, `content_md5`, `git`, `branch`, `commit`, `clone_dir`, `on_mismatch` | fetch (§6) |
| `source.data_copy`, `data_copy_sha256`, `saved_with_data`, `backends`, `compiled_only`, `external_links` (`{name, type}`) | fetch, parse, dataset, reference, golden (HMAC key, §11), relink-scan |
| `source.vba_export`, `source.sample_outputs` (relative to `clone_dir`; names contain spaces, so tools read them from here, never from shell arguments) | parse, reference (§16) |
| `source.licence` | S6.handover (README ownership section, NOTICE) |
| `app.*`, `hosting.*`, `auth.*` | new-app, prompts |
| `auth.groups_to_roles` (`{"<Entra group object id>": "<role>"}`, keyed by object id because the token's groups claim carries ids; null for `local`) | S4 prompts; you build the app's `DJANGO_ENTRA_GROUP_ROLES` setting from it at deploy |
| `target.repo` (`owner/name`), `target.mode` | `push` and `gh` (origin must be `https://github.com/<target.repo>`), integrate |
| `database.primary`, `verify_also` | stage1, relink-scan, equivalence and results (`from-engagement`), purge |
| `database.stage1` (`fresh_copy`, `freeze_window`, `relink_by`, `rollback`, `office_bitness`, `odbc_driver`; null for demos) | stage1 `--dataset fresh-copy`, relink-scan, S3 docs, the go-live section of the S6 target runbook |
| `expected_inventory` | inventory `--expect` (§5) |
| `scope.areas_hint`, `must_keep`, `foundation` (arrays, empty allowed) | prompts (S1, S5) |
| `environment.windows_box`; `environment.windows_box.copies` (array of `{path, what}`, one per place on the box that holds client files; the S3 gate adds the relinked copy) | prompts (`manual_steps`); purge lists `copies` in the destruction record (§15) |
| `assets.screenshots` | driver `skip_if` (S6.visuals, S6.visuals_xlsm) |
| `commercial.milestones` | the S1 quote (payment section; demos carry it for the simulated quote), the S1, S3, S5 and S6 client gates |
| `commercial.rate_usd_per_hour` | inventory, which copies it into inventory.json for estimate and the health check (§5, §15) |
| `data_policy.*`, `commercial.*` | driver checks |
| `secrets.*` | `toolkit/secrets.py`, for mp-data only (below) |

Failure: `load()` exits 2 with `engagement: <json.path>: <reason>` (for example `engagement: source.path: unresolved {{p.data}}`). The driver reports the same as `problems`.

<a id="secret-refs"></a>
### Secret references
- Engagement `secrets` holds references, never values: `secrets.github_push.ref`, `accdb_password_ref`, `stage1_target_url_ref`, `staging_db_url_ref`, `production_db_url_ref` (the go-live load at the S6 client gate, `stage1 --target-db secret:production_db_url_ref`), `entra.client_secret_ref`; also `source.backends[].password_ref`. `secrets.accdb_password_ref` and `source.backends[].password_ref` are reserved: no command resolves them until encrypted sources are specified (parse exits 2 on an encrypted source, §4).
- Forms (pattern `^(env:[A-Z_][A-Z0-9_]*|pass:\S+)$`): `env:NAME` is the line `export NAME=...` in `$HOME/.config/modernization/data.env` (read from the file, never exported); `pass:<path>` is an entry in the `pass` store (its first line). Only the operator and mp-data read either; mp-run hides both (§17).
- `pipeline:toolkit/secrets.py` (S0.workspace) resolves a ref only for a data-plane call: stage1 `--target-db secret:<ref>`, `push` and `gh` (the two reserved password refs above are never resolved). mp-data's toolkit child cannot read data.env or the pass store (its jail hides them, §3), so `toolkit/dataplane.py` resolves exactly the refs the call needs before it starts the child and passes each value only as `MP_SECRET_<FIELD>` in the child's environment: FIELD is the field path upper-cased with each run of characters outside `[A-Z0-9]` replaced by `_` (`MP_SECRET_SECRETS_GITHUB_PUSH_REF`, `MP_SECRET_SECRETS_STAGE1_TARGET_URL_REF`). Inside the jail (`MP_SANDBOX=mp-data`) `toolkit.secrets` reads only that variable. Client sessions deny the Read tool `/proc/**`, where a child's environment could be read (§17).
- A resolved value never appears in prompts, arguments, tracked files, the design zone, logs or summaries. Pipeline and records pushes use your own git and gh credentials: gh's file-stored token (`gh auth login --insecure-storage`, so `gh auth status` names `hosts.yml`), because mp-run hides the keyring with `$XDG_RUNTIME_DIR` (§17).

---

<a id="quiet-output"></a>
## 3. quiet-output

Covers the quiet-output rule of every toolkit command and the `mp-data` data plane. The summary format is §17.

- **Producer:** every toolkit command, through `pipeline:toolkit/quiet.py` (S0.workspace; test `toolkit/tests/test_quiet.py`).
- **Consumers:** mp-data (`scripts/mp-data`, `pipeline:toolkit/dataplane.py`, S0.workspace); agents read only mp-data's stdout.

<a id="toolkit-quiet"></a>
### Toolkit commands
- `MP_QUIET` unset: human-readable output on stdout and stderr.
- `MP_QUIET=1`: stdout carries exactly one line, the summary JSON (§17), written at exit even after an exception. Everything else goes to stderr. No colour, no progress bars.
- `status` follows the exit code: `ok` for 0; `recorded` for 0 when the command wrote a lock instead of comparing (`fetch --record-if-unset`, `inventory --expect`); `no-data` for 0 when `equivalence --allow-empty` selected nothing or `dataset export` skipped the tables of a back end that was not received (§7); `failed` for 1 and 5; `error` for anything else. mp-data itself writes `not-built` (exit 0, below).

<a id="mp-data"></a>
### mp-data

**Data-plane call.** A command string that starts with `mp-data ` and is one simple command: no shell operator, redirection, substitution, subshell, brace group, `cd`, `if` or `for`, no `;`, `&&`, `||`, `|`, a lone `&`, `<`, `>`, backtick or newline, no unbalanced quote, and no `$` other than the literal token `$MP_DB_SUFFIX` (also inside a double-quoted argument). Quoted arguments are allowed (`-k '[orders::'`). Every invocation of mp-data in `pipeline.json` is a data-plane call, one per command string, without `--slot` (the driver adds it and replaces `$MP_DB_SUFFIX`, quoted or not, with the slot).

**Form:** `mp-data [--slot <slot>] [--if-built] <command> <eng> [args]`. Global flags come before the command (a global flag after it exits 2). The engagement is the first argument that ends in `/engagement.json`; a call without one exits 2. `mp-data pin <sha>` and `mp-data pin --status` take no `<eng>`.

**Commands** (`pipeline.json` `data_plane.commands`): `fetch`, `parse`, `dataset`, `guard`, `reference`, `golden`, `stage1`, `equivalence`, `results`, `purge`, `push`, `gh`, `pin`. Subcommand words follow the command (`mp-data reference build`, `mp-data golden record`, `mp-data results summarize`). Anything else exits 2 and runs nothing.

What mp-data does, in order. `scripts/mp-data` (bash) does steps 1 to 4; `toolkit/dataplane.py` does steps 5 to 11.
1. Source `$HOME/.config/modernization/env` (never data.env) and export `MP_DB_SUFFIX=<slot>` (`--slot`, which must match `^[a-z0-9_]{1,24}$`; default `orchestrator`).
2. Read `<eng>` and the sibling `paths.local.json` (with jq). Pick the toolkit root: when the engagement has `bootstrap: true`, the pipeline worktree (`pipeline`); otherwise the reviewed release named in `$HOME/.local/share/modernization/toolkit.pin`, checked out at `$HOME/.local/share/modernization/toolkit@<sha>` by `mp-data pin <sha>` and run with that worktree's own `.venv` and `UV_CACHE_DIR=$HOME/.local/share/modernization/uv-cache` (both synced by pin). mp-run binds all of `$HOME/.local/share/modernization` read-only and client sessions deny it to the Edit and Write tools (§17), so agent commands cannot change the code that reads client data. With no pin it exits 2 (`no reviewed toolkit: run mp-data pin <sha>`). Agents list that pin in `manual_steps` when an extension must touch client data.
3. `--if-built`: when the engagement has `bootstrap: true` and `toolkit/commands/<command>.py` does not exist in the pipeline worktree, print `{"status":"not-built","command":"<command>","exit_code":0,...}` and exit 0 without running anything. With `bootstrap` false it behaves exactly like the plain call (a missing module exits 2). A task's own primary acceptance never uses `--if-built` for the command that task builds.
4. Change to the toolkit root and run `uv run --project <root> python -m toolkit.dataplane <command> <args>`, every argument passed through unchanged.
5. Read `$HOME/.config/modernization/data.env` when present, parsing its `export NAME=value` lines (never sourcing or exporting them), and build the toolkit child's environment: the env-file variables, `MP_DB_SUFFIX`, `MP_QUIET=1`, `MP_TEST_DB_PREFIX=mpdata_test_`, `MP_SANDBOX=mp-data`, a fresh `TMPDIR` for this call inside the data zone (`tempfile.mkdtemp(prefix='tmp-', dir={{p.private}}/logs)`; temporary files of a data-plane call can hold engagement values, and a directory under `/tmp` would be readable by every agent of the same user; removed afterwards, and one that purge already deleted is no error), and the database URLs of the command, each under two names (`MP_DATA_PG_URL` and `PG_URL`, `MP_DATA_MSSQL_URL` and `MSSQL_URL`; §1 Databases):
   - `equivalence`, `reference build|run|selfcheck`, `golden record` (they run agent-written code): the `mp_runner` URLs (`MP_RUNNER_PG_URL`, `MP_RUNNER_MSSQL_URL`);
   - `stage1`, `purge` (toolkit code only): the superuser URLs (`MP_DATA_PG_URL`, `MP_DATA_MSSQL_URL`);
   - every other command: no database URL.
   When data.env lacks the chosen URL, both names are unset, so an `mp_agent` URL never reaches a data-plane command. No other data.env value reaches the child except the secrets of step 8. For `purge`, mp-data also sets `MP_OTHER_DB_NAMES` (the other engagements' db_names, §18), because the jail hides their directories.
6. Create the parent directory of every `--out`, `--report` and `--matrix` path, and the `--junit-dir` directory itself. Output paths, with symlinks resolved, must lie inside `{{p.pipeline}}`, `{{p.target_repo}}`, `{{p.records}}` or `{{p.private}}`, and the two outputs that hold engagement values, `dataset export --out` and `guard --report`, must lie in the data zone; anything else exits 2 and runs nothing.
7. Refuse (exit 2, runs nothing) a `--target-db` that is a literal URL, or `local:<name>` whose name does not start with `mpdata_`. Before `stage1`, `equivalence`, `reference build|run|selfcheck` or `golden record` runs, connect with the superuser URL of each dialect whose URL the command gets and exit 2, running nothing, when any `mpdata_*` database there is owned by a role other than `postgres` or `mp_runner` (SQL Server: `sa` or `mp_runner`); a server that does not answer is left to the command (§1 Databases).
8. Resolve with `toolkit.secrets` exactly the refs the command needs (§2): `push` and `gh` `secrets.github_push.ref`; stage1 `--target-db secret:<field>` that field. `secrets.accdb_password_ref` and `source.backends[].password_ref` are reserved: no command resolves them until encrypted sources are specified (§2). Each value goes to the child only as `MP_SECRET_<FIELD>` in its environment.
9. Run `<root>/.venv/bin/toolkit <command> <args>` in the jail below (no uv inside it), and write the child's full stdout and stderr to `{{p.private}}/logs/<utc-ts>-<slot>-<pid>-<command>.log` (`<utc-ts>` `YYYYMMDDTHHMMSSZ`, `<pid>` the mp-data process; chmod 600, created exclusively), so parallel agents never share a log.
10. Validate the child's summary line against `summary.schema.json` and add `log` (the log path).
11. Print exactly that one line.

<a id="data-plane-jail"></a>
**The jail** (S0.workspace, `toolkit/dataplane.py`; environment item `bwrap`). The child runs under `bwrap --unshare-user --unshare-pid --die-with-parent --ro-bind / / --dev /dev --proc /proc`, then the writable binds, then the masks:
- Read-only: everything not listed below, including the design zone, both repos and `$HOME`. `--dev /dev` gives the child its own `/dev/shm`, so no tmpfs shared with agents is writable; `/tmp` and `/var/tmp` stay read-only.
- Writable (`--bind`, each created first when missing): this engagement's data-zone directories and its `scrub-map.json` (§1); the call's `TMPDIR`; the `--junit-dir` directory and the parent directory of each `--out`, `--report` and `--matrix` path; and per command: `fetch` `{{p.records}}` (source.lock.json) and the toolkit root's `vendor/` (jars, §6); `guard` the pre-commit cache `$HOME/.local/share/modernization/pre-commit`, used as `PRE_COMMIT_HOME`; `golden record` `{{p.target}}/goldens/`; `push` and `gh` the target repo's git directories (`git rev-parse --absolute-git-dir` and `--git-common-dir`); `purge` `{{p.private}}` itself and `{{p.records}}` (purge empties `{{p.private}}`; directories that are mount points in the jail, `{{p.private}}` included, stay, empty).
- Masks: `--tmpfs` over every other engagement's `private/` and `record/` under `$HOME/engagements/` (for every command, purge included: purge gets their db_names in `MP_OTHER_DB_NAMES`, step 5), over the `pass` store directory (`${PASSWORD_STORE_DIR:-$HOME/.password-store}`), and over the directories that hold unix sockets of your own processes: `$XDG_RUNTIME_DIR` (default `/run/user/<uid>`: the session bus and the systemd user manager, rootless Docker and containerd, ssh and gpg agents), `${TMUX_TMPDIR:-/tmp}/tmux-<uid>` and `/run/screen/S-<user>` (each when present). A read-only mount does not stop `connect()` on a unix socket: tested with bubblewrap 0.11.1, a child under `--ro-bind / /` without that tmpfs read the systemd user manager's properties over the session bus, and that manager can start a command outside any jail. Then `--ro-bind /dev/null` over data.env, `/var/run/docker.sock` and the unix socket `DOCKER_HOST` names when it lies outside `$XDG_RUNTIME_DIR` (each when present).
- The host network is kept, so the 127.0.0.1 databases work.

So code that a data-plane call starts (the Django app under `equivalence --adapter django`, app-function rewrites under `reference` and `golden`) can read this engagement's data but can write only into the data zone, `TMPDIR` and the call's declared outputs, and never sees data.env, the pass store, Docker, your session sockets or another engagement. A data-plane call therefore never writes the design zone unless that path is a declared output: seed reference databases and `golden record --dataset seeds` run as `uv run toolkit` (§9, §11), and a data-plane `equivalence` run keeps pytest-regressions files in a temp dir (§12).

- Corpus filter: when `{{p.private}}/corpus/strings.txt` exists (UTF-8, one string per line: the engagement's distinct data strings of at least `stack.guard.corpus_min_string_len` = 8 characters, written by guard), a summary line that contains any of them is withheld.
- Exit code = the toolkit's. When the summary is missing, invalid or withheld, mp-data prints its own summary with `status` `error` and exits 2 if the toolkit had exited 0.
- mp-data never runs inside mp-run: the data zone and data.env are hidden there (§17). Data-plane calls are always top-level commands.

Commands that only ever run through mp-data. `push` and `gh` are toolkit subcommands (S0.workspace, `toolkit/commands/push.py` and `gh.py`, with `toolkit/secrets.py`); they resolve secrets, so agents never run them as `uv run toolkit`. `pin` is mp-data's own.
- `mp-data push <eng> --branch <branch>`: pushes local branch `<branch>` of `{{p.target_repo}}` to `origin` `<branch>`: `git -c core.hooksPath=/dev/null -c core.fsmonitor=false -c credential.helper= -c credential.helper=<one-shot helper> push origin refs/heads/<branch>:refs/heads/<branch>` with `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`, so only the repo's own config applies. The one-shot helper answers only for `https://github.com`, with the token from `secrets.github_push.ref`, so the token never reaches argv, output, the log or a file. Exit 2 when `target.mode` is not `github` or the ref is unset, on a branch outside `^[A-Za-z0-9._/-]+$`, on any force flag, on an origin url or pushurl other than `https://github.com/<target.repo>` (with or without `.git`), and when the repo's own git config sets `url.*.insteadOf`, `url.*.pushInsteadOf`, `remote.origin.proxy` or any `http.*` proxy or ssl key. Repo `credential.*` keys are allowed (the empty `credential.helper` resets them; the runbook sets an empty helper and `useHttpPath` itself). The summary names the branch and the pushed commit. The runbook uses it for stage branches, gate commits, dependency adds, change-order merges and `main`. The driver's integrate prompt uses `mp-data --slot <slot> push ...` for a client target repo (`bootstrap` false, `target.mode` github) and `git push` otherwise.
- `mp-data gh <eng> -- <pr|run> <action> [args]`: runs `gh` in `{{p.target_repo}}` with `GH_REPO=<target.repo>`, `GH_CONFIG_DIR` set to a new empty directory under `TMPDIR` and `GH_TOKEN` from the same ref, all in that child's environment only. Allowed: `pr create|view|list|checks|merge` and `run list|view`. Exit 2 on any other subcommand or action, on `-R`/`--repo`, on an origin other than `https://github.com/<target.repo>`, and on a file argument (`--body-file`, `-F`, `--template`) that resolves into the data zone, data.env or another engagement's directory (gh would upload it). The summary's `messages` carry gh's stdout lines (at most 50, each cut to 200 characters), so the runbook reads `pr list --json number --jq length`, `pr view --json url,headRefOid`, the URL that `pr create` prints and `run view --log-failed` there; stderr goes to the log; the token never appears.
- `mp-data pin <sha>`: you only, in a terminal (it refuses when stdin or stdout is not a terminal, which an agent's Bash call never has). It works in the git checkout of the current directory (a pipeline clone or worktree; exit 2 otherwise) and refuses a sha that is not an ancestor of `origin/main` after `git fetch origin` there. Then it runs `git worktree add --detach $HOME/.local/share/modernization/toolkit@<sha> <sha>`, `uv sync --locked --all-groups` and `uv run toolkit fetch --jars` there (with `UV_CACHE_DIR=$HOME/.local/share/modernization/uv-cache`), runs that release's `scripts/install-mp-data.sh` (so the installed mp-data, mp-run and mp-bash-hook come from the reviewed release) and writes the sha to `$HOME/.local/share/modernization/toolkit.pin`. `mp-data pin --status` prints the pinned sha in a summary and exits 1 when nothing is pinned.

```json
{"status":"ok","command":"dataset export","engagement":"northwind-demo","exit_code":0,"counts":{"tables":28,"rows":3155},"messages":[],"log":"$HOME/engagements/northwind-demo/private/logs/20261016T101500Z-orchestrator-48211-dataset.log"}
```

---

<a id="object-model"></a>
## 4. object-model

- **Path:** `{{p.private}}/out/model/` (design zone, scrubbed): `schema.json`, `objects.json`, `procedures.json`, `datamacros.json`, `code/<module>.txt` (file-safe module name). Schema `pipeline:toolkit/schemas/model.schema.json`; prose `pipeline:docs/formats/model.md`.
- **Producer:** S0.parse, `mp-data parse <eng> --strict --out {{p.private}}/out/model`. Parse scrubs before writing.
- **Consumers:** S0.healthcheck, S0.dataset, S1.areas, S1.synthesize, S2.reference, S2.reference_xlsm, S2.synth, S3.relink_scan, S3.move, S4.models, S6.visuals, S6.visuals_xlsm.

**Neutral column types** (one vocabulary for every source kind): `text`, `memo`, `byte`, `integer`, `longinteger`, `autonumber`, `single`, `double`, `currency`, `decimal`, `datetime`, `yesno`, `hyperlink`, `oleobject`, `complex`, `guid`.

`schema.json`:
```json
{
  "format_version": 1,
  "source_kind": "accdt",
  "tables": [
    {
      "name": "Orders",
      "role": "data",
      "columns": [
        {"name": "OrderID", "type": "autonumber", "size": null, "precision": null, "scale": null, "required": true},
        {"name": "CustomerID", "type": "longinteger", "size": null, "precision": null, "scale": null, "required": false},
        {"name": "Notes", "type": "memo", "size": null, "precision": null, "scale": null, "required": false}
      ],
      "primary_key": ["OrderID"],
      "indexes": [{"name": "CustomerID", "columns": ["CustomerID"], "unique": false}]
    }
  ],
  "relationships": [
    {"name": "CustomersOrders", "from_table": "Orders", "from_columns": ["CustomerID"], "to_table": "Customers", "to_columns": ["CustomerID"], "enforce": true, "cascade_update": false, "cascade_delete": false}
  ]
}
```

`objects.json`, `procedures.json`, `datamacros.json`:
```json
{
  "format_version": 1,
  "queries": [{"name": "qryOrderTotals", "storage": "design", "query_type": "select", "design": {}, "sql": null, "params": [], "functions": ["Nz"]}],
  "forms": [{"name": "frmOrder", "record_source": "Orders", "has_code_behind": true,
    "sections": [{"name": "FormHeader", "kind": "form_header", "height": 720}, {"name": "Detail", "kind": "detail", "height": 5040}],
    "tab_pages": [{"tab_control": "tabMain", "name": "pgLines", "caption": "Lines", "index": 0}],
    "controls": [
      {"name": "lblTitle", "type": "Label", "section": "FormHeader", "caption": "Order", "left": 360, "top": 120, "width": 2880, "height": 420, "control_source": null, "row_source": null, "source_object": null, "page": null, "events": []},
      {"name": "cboCustomer", "type": "ComboBox", "section": "Detail", "caption": null, "left": 1440, "top": 360, "width": 2880, "height": 315, "control_source": "CustomerID", "row_source": "Customers", "source_object": null, "page": null, "events": ["AfterUpdate"]},
      {"name": "sfrLines", "type": "Subform", "section": "Detail", "caption": null, "left": 360, "top": 1200, "width": 9000, "height": 3000, "control_source": null, "row_source": null, "source_object": "sfrOrderLines", "page": "pgLines", "events": []}
    ]}],
  "reports": [{"name": "rptInvoice", "record_source": "qryInvoice", "has_code_behind": false, "sections": [], "tab_pages": [], "controls": []}],
  "macros": [
    {"name": "AutoExec", "kind": "standalone", "owner": null, "actions": [{"action": "OpenForm", "args": {"FormName": "frmOrder"}}]},
    {"name": "frmOrder.Load", "kind": "embedded", "owner": "form:frmOrder.Load", "actions": [{"action": "GoToRecord", "args": {"Record": "New"}}]}
  ]
}
```
```json
{
  "format_version": 1,
  "modules": [
    {"name": "modOrders", "kind": "standard", "nonblank_lines": 120, "code_file": "code/modOrders.txt",
     "procedures": [{"name": "PostOrder", "kind": "Sub", "start": 10, "end": 42, "refs": {"tables": ["Orders"], "queries": [], "forms": ["frmOrder"], "docmd": ["OpenReport"], "dao": true}}]}
  ]
}
```
```json
{"format_version": 1, "datamacros": [{"table": "Orders", "event": "BeforeChange", "actions": [{"action": "RaiseError", "args": {"number": "1"}}]}]}
```

Rules:
- `role` is `data` or `documentation`. `query_type` is `select`, `union`, `crosstab`, `action` or `pass-through`; `storage` is `design` (grid) or `sql`.
- Module `kind`: `standard`, `class`, `form`, `report`, `document`, `userform`. `code/` holds each module's scrubbed VBA, UTF-8, LF (parse accepts CRLF and LF). Code-behind modules are named as Access names them (`Form_<form>`, `Report_<report>`).
- Encodings are detected per file (UTF-16LE with BOM for forms, reports, queries, macros; cp1252 for modules).
- **Access form and report layout** (S6.visuals reads it): `sections[]` with `kind` `form_header`, `page_header`, `detail`, `page_footer`, `form_footer` (reports also `group_header`, `group_footer`) and `height`; `controls[]` with `type` (the Access control type name), `name`, `section`, `caption` (scrubbed), `left`, `top`, `width`, `height` (integers, twips), `source_object` (subforms), `page` (the tab page a control sits on, or null); `tab_pages[]` per tab control in index order.
- **Macros** (Access): `macros[]` entries are `{name, kind: "standalone" | "embedded", owner: null | "form:<form>.<event>" | "report:<report>.<event>", actions: [{action, args: {<argument name>: <object name or scrubbed literal>}}]}`. A standalone macro is a macro object (`owner` null; parse reads its SaveAsText block, or the `.accdt` macros/ part). An embedded macro is an event property of a form, a report or one of their controls set to `[Embedded Macro]`; it is named `<form>.<event>` or `<report>.<event>`, the same as its `owner` without the `form:` or `report:` prefix, and for a control's event `<event>` is `<control>.<event>`. `actions` keep the macro's order. `controls[].events` marks an event that an embedded macro handles as `<event>:macro` (`Click:macro`).
- **Linked tables** (Access): `objects.json` lists every linked table with its back-end kind (`access`, `odbc`, `sharepoint`, `excel` or `text`) and a back-end number (the connect string itself is scrubbed). Its other non-data facts: query storage, objects with code behind, `has_cached_value` per formula cell (xlsm), and the file count per declared `sample_outputs` folder (counts only, never file names).
- **xlsm `schema.json`:** one table per ListObject (`sheet` and `ref` added; `columns` in header order).
  - Column types are inferred from cached values (`datetime`, `decimal`, `longinteger`, `text`).
  - Calculated columns get `"computed": true` and `"formula"`: the column's calculatedColumnFormula as written, structured references kept.
  - A table without business data (InvoiceGenerator: VBAGlossary) gets `"role": "documentation"`.
  - Inferred keys and relationships get `"inferred": true`: a unique non-blank column is a key; a column whose values all appear in another table's key is a foreign key. S1 confirms them in `data-model.json` (§8).
  - List validations give `"choices_from"`: the validation's list source as written (a literal list or a range reference).
- **xlsm formula blocks** (`objects.json` `formula_blocks`): contiguous formula ranges outside tables, with the address of every cell and its formula as written. With the ListObjects, defined names and label cells they are everything S2.reference_xlsm needs to rebuild a seed workbook (§9).
- **xlsm `objects.json`** gains `sheets` (`role` input, template, calculation, dashboard or documentation), `userforms` (name, caption, controls with name, type and caption from olevba; with `source.vba_export` set, also each control's position from the exported `.frm` text; the `.frx` layout is not parsed), `formula_blocks`, `defined_names` (broken `#REF!` names get `"broken": true` and are warnings, not errors), `charts`, `buttons`, `macro_assignments`, `activex`, `comments` (counts per sheet) and `vba_references`.
- **xlsm layout** (S6.visuals_xlsm and the S2 seed workbooks read it; nothing opens the source workbook for it): per sheet `column_widths` and `row_heights` (non-default ones, with the defaults), `merged` ranges, `labels`, `list_objects` (`name`, `ref`, `headers`); `charts[]` with `sheet`, `name`, `type`, `title` and `anchor` (`from` and `to` cells); `buttons[]` (form-control buttons) with `sheet`, `name`, `caption`, `macro` and `anchor`.
- **Label cells:** constant text cells outside ListObjects, on sheets with role input, template or dashboard, that no formula and no VBA `Range` or `Cells` literal reads or writes, at most 80 characters, scrubbed. Any other constant cell is recorded by position only, with value null; constant input cells that formulas read become input-cell params (§9), never model values.
- **Names that rules.json and the inventory share:** calculated columns `<Table>[<Column>]`, formula blocks `<Sheet>!<A1 range>`, tables (Access tables, xlsm ListObjects) as type `table` (§5).
- **Cached values never enter the model.** Parse writes them to `{{p.private}}/workbook/` (data zone).
- **Scrub:** credentials in connect strings (`PWD=`, `Password=`, `UID=`), credential URLs and gitleaks-style hits become `<SECRET-n>`; emails `<EMAIL-n>`; phone numbers `<PHONE-n>`. The map goes to `{{p.private}}/scrub-map.json`. Agents use tokens as they are.
- With `source.data_copy` set, parse also compares the design's tables, columns and types with the data copy and prints per-table counts only.

Failure:
- `parse --strict` exits 1 on any parse error or unknown design part (warnings are allowed) and on a design/data-copy mismatch. The `--fail-on` conditions `compiled-only` (no VBA source: `.accde`/`.mde` or `source.compiled_only`), `missing-backend` (a linked table whose back-end file was not received: neither `source.data_copy` nor a `source.backends` entry names an existing file) and `unresolved-link` (a linked table whose connect string cannot be classified) exit 1; `multi-backend` (tables linked to more than one data back end) exits 2 with `more than one data back end: custom quote` (§2). The `--fail-on` conditions are checked after the model is written, so S0.healthcheck can still report them; S0.parse's own acceptance runs parse without `--fail-on`, and the S0 exit criterion enforces it. Exit 2 when the source is missing.
- parse exits 2 with `encrypted source: custom quote` when the source file or a back end is encrypted or password-protected (no codec is pinned and the bridge takes no password).
- `scrub --check {{p.private}}/out/model` exits 1 when anything that scrub would replace remains.

---

<a id="inventory"></a>
## 5. inventory

- **Paths:** `{{p.private}}/out/inventory.json` (design zone); `{{p.records}}/inventory.lock.json`; `expected_inventory` in engagement.json.
- **Producer:** S0.healthcheck, `uv run toolkit inventory <eng> --model {{p.private}}/out/model --out {{p.private}}/out/inventory.json --expect`.
- **Consumers:** healthcheck, estimate, S1.areas (scout), rules-coverage (§8), S1.synthesize, the S0 gate.

```json
{
  "format_version": 1,
  "engagement": "northwind-demo",
  "source": {"kind": "accdt", "sha256": "5c38b53e1600c2e21ddd91cb17ce6719615c6a32deff5441fffbcb239df6e5e2"},
  "expectation": "compared",
  "rate_usd_per_hour": null,
  "rule_bearing_kinds": ["procedure", "data_macro", "macro", "query", "form", "report"],
  "counts": {"tables": 28, "queries": 65, "queries_design_grid": 63, "queries_sql_text": 2, "forms": 40, "reports": 15, "modules": 18, "class_modules": 1, "macros": 1, "data_macro_files": 20, "data_macros": 20, "objects_with_code_behind": 48, "vba_nonblank_lines": 6886},
  "objects": [
    {"type": "table", "name": "Orders", "metrics": {"columns": 14}},
    {"type": "query", "name": "qryOrderTotals", "metrics": {"joins": 1, "outer_joins": 0, "groups": 1}},
    {"type": "procedure", "name": "modOrders.PostOrder", "metrics": {"lines": 30}}
  ],
  "edges": [{"from": "query:qryOrderTotals", "to": "table:Orders"}],
  "facts": {"source_bytes": 12895205, "data_copy_bytes": null, "data_backends": 1, "linked_tables": {}, "external_links": {}, "compiled_only": false, "vba_present": true}
}
```

`rate_usd_per_hour`: the engagement's `commercial.rate_usd_per_hour`, or null when unset (Northwind: null). `estimate` takes only inventory.json, so this is how the engagement's rate reaches it (§15).

`facts` (for the health check and the Stage 1 estimate; no names of data, no values): `source_bytes` and `data_copy_bytes` (null without a data copy), `data_backends` (the number of data back ends), `relationships` (Access kinds: the number of relationships in schema.json; not measured for Northwind, so the example leaves it out), `linked_tables` and `external_links` (counts by kind: `access`, `odbc`, `sharepoint`, `excel`, `text`), `compiled_only`, `vba_present`, and for xlsm `vba_office_references` (the Outlook or Word references by name) and `defined_names_broken`.

Object types and names:

| Kind | Types (`name` form) |
|---|---|
| Access | `table`, `query`, `form`, `report`, `module` (object name); `macro` (a standalone macro by object name, an embedded one as `<form>.<event>` or `<report>.<event>`, §4); `procedure` (`<module>.<procedure>`); `data_macro` (`<table>.<event>`) |
| xlsm | `worksheet`, `table` (ListObject), `userform`, `module`, `defined_name` (name); `procedure` (`<component>.<procedure>`); `calculated_column` (`<Table>[<Column>]`); `formula_block`, `data_validation` (`<Sheet>!<A1 range>`); `chart` (`<Sheet>!<chart name>`) |

Every table is an object of type `table`. These type names are the ones rules.json uses in `objects[].type` and `rules[].sources[].type` (§8).

`rule_bearing_kinds`: Access `procedure`, `data_macro`, `macro`, `query`, `form`, `report`; xlsm `procedure`, `userform`, `calculated_column`, `formula_block`, `data_validation`, `chart`. `metrics` hold integers only. `edges` use `<type>:<name>` (query to tables, queries and functions; form to record source and procedures; procedure to tables, queries and forms).

Access count keys (Northwind values in brackets):
- `tables` local tables [28]; `queries` saved queries [65] = `queries_design_grid` [63] + `queries_sql_text` [2]; `forms` [40]; `reports` [15]; `modules` standard plus class [18]; `class_modules` [1]; `macros` standalone macro objects [1]; `embedded_macros` macros embedded in form, report and control events (not measured for Northwind; its `expected_inventory` does not list the key, and `--expect` compares only the keys listed there).
- `data_macro_files`: `.axl` files under `template/database/objects/dataMacros/` only [20]. The 55 `.axl` files under `objects/properties/` are object metadata and never count.
- `data_macros`: data macros (event handlers) in those files [20].
- `objects_with_code_behind`: forms and reports with a CodeBehindForm/CodeBehindReport section [48].
- `vba_nonblank_lines`: non-blank lines, excluding `Attribute` lines, in the modules plus the code-behind sections [6886].
- Kinds `accdb` and `mdb` count `tables` and `queries` only. A key a kind cannot provide is left out, never 0.

xlsm count keys (InvoiceGenerator values in brackets). The `_counting_rules` and `_vba_line_rule` strings in `engagements/invoicegen-demo/engagement.json` are part of this contract:
- `worksheets` / `hidden_worksheets` / `chartsheets` [7 / 0 / 0]; `tables` (ListObjects) [3]; `table_columns` per table [InvoiceTable 27, CustomerTable 8, VBAGlossary 8]; `calculated_columns` [6].
- `defined_names` all `<definedName>` in xl/workbook.xml, including `_xlnm.*` [6]; `defined_names_broken` (refers_to contains `#REF!`) [2]; `data_validations` [2].
- `formula_cells` (openpyxl data_type `f`) [335]; `formula_cells_by_sheet` [Dashboard 5, Invoice 299, Template 6, Calculation 25]; `formula_cells_without_cached_value` [3]; `array_formula_cells` [0].
- `charts` [1]; `form_buttons` (xl/ctrlProps objectType Button) [4]; `macro_assignments` (`macro=` in xl/drawings) [5]; `activex_controls` (xl/activeX/*.xml) [2]; `cell_comments` [18].
- `vba_components` [13] = `vba_standard_modules` [1] + `vba_class_modules` [0] + `vba_document_modules` [8] + `vba_userforms` [4]; type from the PROJECT stream (`Module=`, `Class=`, `Document=`, `BaseClass=`).
- `vba_nonblank_lines` [546]: per component code stream as olevba extracts it, lines non-blank after strip and not starting with `Attribute `; comment lines count. `vba_procedures` [24]; `userform_controls` (olevba form sites) [53]; `vba_references` (REFERENCENAME records) [6].
- `sample_outputs` {pdf 3, xlsx 3, msg 2}.

`inventory --expect`:
1. `expected_inventory` has numeric keys (keys starting with `_` ignored; nested objects compared key by key): compare; any difference exits 1 naming the key.
2. Else `{{p.records}}/inventory.lock.json` exists: compare with it the same way.
3. Else write the lock, set `"expectation": "recorded"`, print `recorded, not compared; approve at the gate` and exit 0 (summary status `recorded`). The S0 gate approves the recorded counts.

```json
{"format_version": 1, "recorded": "2026-10-20", "source_sha256": "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb", "counts": {"tables": 12, "queries": 30, "forms": 18, "reports": 9}}
```

Failure: exit 2 when the model is missing; exit 1 on a count difference (listing key, expected and counted) or when the lock's `source_sha256` differs from the engagement's `source.sha256` (the pin, or `source.lock.json` through the loader). Inventory counts from the model only and never reads the source file.

---

<a id="source-lock"></a>
## 6. source-lock

- **Path:** `{{p.records}}/source.lock.json`.
- **Producer:** S0.workspace, `mp-data fetch <eng> --verify --record-if-unset`, only when the engagement has no pin.
- **Consumers:** `fetch --verify`, the engagement loader (§2), golden (which data file keys the HMAC, §11), purge (destruction record), the S0 gate.

```json
{
  "format_version": 1,
  "recorded": "2026-10-20",
  "source": {"path": "sources/c2026001_fe.accdt", "bytes": 4194304, "sha256": "3e23e8160039594a33894f6564e1b1348bbd7a0088d42c4acb73eeaed59c009d", "git": null},
  "data_copy": {"path": "sources/data/c2026001_be.accdb", "bytes": 10485760, "sha256": "2e7d2c03a9507ae265ecf5b5356885a53393a2029d241394997265a1a25aefc6"},
  "backends": []
}
```
- Paths are relative to `{{p.private}}`. A git source records `"git": {"url": "...", "branch": "...", "commit": "<40 hex>"}`.
- `--record-if-unset` records the source and, when `source.data_copy` is set, the data copy: its hash is `source.data_copy_sha256` from then on (the loader fills it from `data_copy.sha256`). Each back end in `source.backends` is recorded the same way.
- Pins come from engagement.json first (`source.bytes`, `sha256`, `commit`, `data_copy_sha256`, `backends[].sha256`), then from the lock. Demos keep their pins in engagement.json (northwind: bytes 12895205, sha256 `5c38b53e…df6e5e2`, plus `last_modified`, `etag`, `content_md5`; invoicegen: commit `831e527251f11397fae411195ed00232443cb107`, bytes 344254, sha256 `c0a03b76e84a587b3623a09e325f7033e7889f193f4a0a7fd14def15642a33e0`).
- URL sources: a HEAD request first; when the engagement pins `last_modified`, `etag` or `content_md5`, any difference exits 1 before anything is downloaded. Then download into `sources/`, and check size and sha256. Git sources: clone into `source.clone_dir` at the pinned commit, then check `source.path` inside it.
- `fetch --verify` also restores the vendor jars, for every source kind (xlsm too: from the northwind S2 on, `toolkit/tests/test_reference.py` starts a JVM in every `uv run pytest -q toolkit` run): any missing or wrong jar of `stack.reference_engine.jars` is downloaded into the toolkit root's `vendor/`, as `fetch --jars` does. That `vendor/` is one of fetch's declared outputs in the jail (§3). For demos the toolkit root is the pipeline worktree, so the prepare list's fetch line covers its gitignored `vendor/`. For clients it is the pinned release (whose `vendor/` `mp-data pin` fills), so the S0 (clients only), S1 and S2 prepare lists also run `uv run toolkit fetch --jars` for the pipeline worktree's own `vendor/`, which later stages keep (it is gitignored).
- `fetch --jars`: downloads `stack.reference_engine.jars` into `vendor/` (Google Maven mirror first, repo1 second) and checks each sha256. It needs no engagement and reads no data.

Failure:
- Any mismatch exits 1 and reports blocked: `on_mismatch` is "report blocked; never update pins without the user".
- No pin and no `--record-if-unset`: exit 2.
- With `--record-if-unset` and no pin: write the lock, exit 0 (status `recorded`, `recorded; approve at the S0 gate`).

---

<a id="dataset-directory"></a>
## 7. dataset-directory

- **Layout:** `<dir>/dataset.json` plus `<dir>/tables/<file-safe table>.csv`. Reader and writer: stdlib-only `pipeline:harness/dataset.py` (vendored, so the app can read datasets); `toolkit/dataset.py` wraps it. Schema `toolkit/schemas/dataset.schema.json`; prose `docs/formats/dataset.md` (all S0.dataset).
- **Instances:** engagement `{{p.private}}/datasets/engagement/` (S0.dataset, `mp-data dataset export <eng> --out {{p.private}}/datasets/engagement`); seeds `{{p.target}}/goldens/synthetic/seed-N/input/` (S2.synth).
- **Consumers:** reference build (S2.reference, S2.reference_xlsm), golden record (S2.golden), the harness `load_dataset` (S2.harness), stage1 (S3.move), `import_dataset`, `DjangoAdapter` and `load_seed` (S4.data, S5), S5.e2e (seed-1), wireframe-xlsm (seed-1).
- `harness/dataset.py` holds `format_value(column type, value)`, the one implementation of the value rules below; `toolkit.dataset` re-exports it, and stage1's per-table digests use it.
- `toolkit.dataset.open_backend(path, schema)` returns a reader with `tables()` and `iter_rows(table)` for an Access back-end file at any path, through the S2 Jackcess bridge (`toolkit.reference.bridge`; exit 2 naming the bridge until S2.reference builds it). stage1 `--dataset fresh-copy` uses it.

```json
{
  "format_version": 1,
  "name": "seed-1",
  "kind": "synthetic",
  "seed": 1,
  "schema_sha256": "18ac3e7343f016890c510e93f935261169d9e3f565436429830faf0934f4f8e4",
  "load_order": ["Customers", "Order Details"],
  "tables": [
    {"name": "Customers", "file": "tables/Customers.csv", "rows": 50, "sha256": "3f79bb7b435b05321651daefd374cdc681dc06faa65e374e38337b88ca046dea",
     "columns": [{"name": "CustomerID", "type": "autonumber"}, {"name": "Company", "type": "text"}, {"name": "Active", "type": "yesno"}, {"name": "Balance", "type": "currency"}, {"name": "Created", "type": "datetime"}],
     "omitted_columns": []},
    {"name": "Order Details", "file": "tables/Order_Details-fe3ac0d4.csv", "rows": 120, "sha256": "252f10c83610ebca1a059c0bae8255eba2f95be4d1d7bcfa89d7248a82d9f111",
     "columns": [{"name": "OrderID", "type": "longinteger"}, {"name": "Quantity", "type": "integer"}],
     "omitted_columns": [{"name": "Attachments", "type": "complex", "reason": "complex"}]}
  ]
}
```
```
CustomerID,Company,Active,Balance,Created
1,Example Co,1,12.5,2024-03-01T00:00:00
2,\N,0,0,2024-03-02T09:30:00
3,"Smith, Jones",1,-4.25,\N
```

`dataset.json`:
- `name`: `engagement` or `seed-N`. `kind`: `engagement` or `synthetic`. `seed`: N, or null for the engagement.
- `schema_sha256`: sha256 of the bytes of `{{p.private}}/out/model/schema.json` used.
- `load_order`: every table, parents before children (foreign-key order).
- `tables[]`: `file` (file-safe name), `rows`, `sha256` of the CSV bytes, `columns` in schema order with neutral types, `omitted_columns` with `reason` `complex`, `oleobject` or `computed`.

CSV values:
- UTF-8 without BOM, RFC 4180 (comma, `"` quoting when needed, CRLF). Header = the original column names in schema order.
- NULL is `\N`. An empty field is an empty string. A text that is exactly `\N` (or more backslashes then `N`) gets one extra leading backslash; readers strip it.
- `datetime` `YYYY-MM-DDTHH:MM:SS` (no zone, no fraction; date-only values get `T00:00:00`). `yesno` `1`/`0`. `byte`, `integer`, `longinteger`, `autonumber` plain integers.
- `currency`, `decimal`: plain decimal with `.`, no exponent, no thousands separator, as stored.
- `single`: the shortest decimal that round-trips through IEEE binary32. `double`: Python `repr`. NaN and infinity are errors.
- `guid`: lower-case `8-4-4-4-12`, no braces. `text`, `memo`, `hyperlink`: as stored.
- `complex` and `oleobject` columns are omitted and listed. xlsm: calculated columns are omitted with reason `computed`; documentation tables are not in the dataset.

Failure: `dataset validate <dir> --schema <schema.json>` exits 1 on a missing table or column, a wrong type, a row-count or sha256 mismatch, or a foreign-key orphan; exit 2 when `dataset.json` is missing. It accepts a seed that omits computed columns (listed in `omitted_columns` with reason `computed`) and leaves out tables whose schema role is `documentation` (how xlsm seeds look), but rejects an omitted ordinary column. `dataset export --check` exits 1 when a fresh export differs. Export exits 2 with `more than one data back end: custom quote` when tables are linked to any back end other than the one data back end (§2).

No data: when `source.kind` is `accdt` or `vcs-export`, `saved_with_data` is not true and `data_copy` is null, export writes a `dataset.json` with an empty `tables` list and exits 0 (status `ok`, `counts.tables` 0, message `no data in the source`). So S0 can pass for a health check on a design-only file; the driver refuses such an engagement for S2 and S3, and `reference build --dataset engagement` exits 2 (§9).

Back end not received (§2): export skips the tables linked to a back end that was not received (they are left out of `tables` and `load_order`), writes the rest and exits 0 with summary status `no-data`, its `messages` giving the number of skipped tables. S2 and S3 never get such a dataset: their prepare lists stop at `parse --fail-on missing-backend`.

---

<a id="rules-json"></a>
## 8. rules-json

- **Paths:** `{{p.target}}/analysis/rules/<area>.json` (`kind` `area`, one per S1 area), `{{p.target}}/analysis/rules.json` (`kind` `merged`), plus `rules.md`, `keep-drop.md` (generated), `interfaces.md`, and for xlsm `data-model.json`. Schemas `pipeline:toolkit/schemas/rules.schema.json` and `data-model.schema.json` (S1.tooling).
- **Producers:** S1.areas (area files), S1.synthesize (merged files), S1.change_order (amendments after signature).
- **Consumers:** rules-lint, rules-coverage, rules-render, spec-check, estimate `--rules`; the orchestrator (S5 items); S2.synth (`lookups`); S2.reference (unit objects); S2.golden (areas, keep); S3.relink_scan; S4.models (`objects`, `relations`, `lookups`); S5.features (`rules`, `services`); S5.e2e (`workflows`); S6.report; S6.visuals (`workflows[].forms`).

```json
{
  "format_version": 1,
  "kind": "merged",
  "engagement": "northwind-demo",
  "spec_version": "1.0",
  "areas": [
    {"id": "orders", "title": "Orders and invoicing", "status": "kept", "queries": ["qryOrderTotals", "qryInvoice"], "forms": ["frmOrder"], "reports": ["rptInvoice"], "procedures": ["modOrders.PostOrder"], "tables": ["Orders", "OrderLines"]},
    {"id": "purchasing", "title": "Purchasing", "status": "kept", "queries": ["qryOpenPOs"], "forms": [], "reports": [], "procedures": [], "tables": ["PurchaseOrders"]},
    {"id": "admin_tools", "title": "Admin tools", "status": "dropped", "queries": ["qryAudit"], "forms": [], "reports": [], "procedures": [], "tables": []}
  ],
  "rules": [
    {"id": "R-orders-001", "area": "orders", "statement": "An order cannot ship before it is invoiced.", "trigger": "form:frmOrder.BeforeUpdate", "sources": [{"type": "procedure", "ref": "modOrders.PostOrder"}], "tables": ["Orders"], "confidence": "high", "test_kind": "rule", "questions": []},
    {"id": "R-orders-002", "area": "orders", "statement": "The invoice total is the sum of the line amounts.", "trigger": "query:qryInvoice", "sources": [{"type": "query", "ref": "qryInvoice"}, {"type": "query", "ref": "qryOrderTotals"}], "tables": ["OrderLines"], "confidence": "high", "test_kind": "equivalence", "questions": ["Q-orders-001"]}
  ],
  "objects": [
    {"type": "table", "name": "Orders", "decision": "keep", "area": "orders", "target_component": "legacy.models", "reason": null},
    {"type": "query", "name": "qryOrderTotals", "decision": "keep", "area": "orders", "target_component": "orders.saved_queries", "reason": null},
    {"type": "query", "name": "qryInvoice", "decision": "keep", "area": "orders", "target_component": "orders.reports", "reason": null},
    {"type": "query", "name": "qryOldReport", "decision": "drop", "area": "orders", "target_component": null, "reason": "duplicate of qryInvoice"},
    {"type": "query", "name": "qryOpenPOs", "decision": "keep", "area": "purchasing", "target_component": "purchasing.saved_queries", "reason": "a plain list of open purchase orders; its unit proves it, no rule"},
    {"type": "query", "name": "qryAudit", "decision": "out_of_scope", "area": "admin_tools", "target_component": null, "reason": "admin only"}
  ],
  "lookups": [{"table": "Orders", "column": "Status", "copy_values": true, "values": null, "rule": "R-orders-001"}],
  "relations": [{"table": "OrderLines", "fk": "OrderID", "on_delete": "CASCADE", "rule": "R-orders-002"}],
  "workflows": [{"id": "order_to_ship", "title": "Order to invoice to shipping", "forms": ["frmOrder"], "reports": ["rptInvoice"], "rules": ["R-orders-001"], "steps": ["enter order", "print invoice", "mark shipped"]}],
  "services": [{"name": "post_order", "area": "orders", "inputs": ["order id"], "outputs": ["invoice id"], "errors": ["not invoiced"]}],
  "open_questions": [{"id": "Q-orders-001", "text": "Are discounts applied before tax?", "status": "open"}],
  "signed": null
}
```

Field rules (no other keys; `additionalProperties` false):
- `kind` `area` or `merged`; `engagement` = the engagement id. `spec_version` `"1.0"`; each change order run (S1.change_order) raises the minor part (`1.1`, `1.2`).
- `areas[]`: `id` follows §1 (area files have exactly one entry, whose id is the item id; the merged file has exactly the S1 item ids). `status` `kept` or `dropped`. The lists hold inventory object names (xlsm: `forms` holds userforms; calculated columns, formula blocks, validations and charts appear only in `objects[]`).
- `rules[]`: `id` `R-<area>-NNN`, `area` an `areas[].id`, `statement` paraphrased (never copied), `trigger` (`form:<form>.<event>`, `report:<report>.<event>`, `datamacro:<table>.<event>`, `macro:<name>` (a standalone macro; an embedded one uses the `form:` or `report:` form of its owner, §4), `procedure:<module>.<procedure>`, `query:<query>`, `cell:<Sheet>!<A1>`, `schedule:<when>` with `<when>` `startup`, `timer:<form>`, `ontime:<procedure>` or `task:<name the client gives>`), `sources[]` `{type, ref}` = an inventory object type and name (§5), `confidence` `high`, `medium` or `low`, `test_kind` `rule`, `equivalence` or `e2e`, `questions` = `Q-` ids.
- `objects[]`: `type` and `name` exactly as in the inventory (§5: tables as `table`, procedures `<module>.<procedure>`, data macros `<table>.<event>`, calculated columns `<Table>[<Column>]`, formula blocks and data validations `<Sheet>!<A1 range>`, charts `<Sheet>!<chart name>`); `decision` `keep`, `drop` or `out_of_scope`; `target_component` required for `keep` (`<area>.<module>`, `legacy.models` or `foundation:<what>`); `reason` required for `drop` and `out_of_scope`. For a kept rule-bearing object that no rule cites in `sources`, `reason` says why it holds no rule (coverage below); otherwise it is null. S4.models' coverage check reads the objects with `type` `table` and `decision` `keep` (Access) or `data-model.json` `tables[].name` (xlsm).
- `lookups[]` `{table, column, copy_values, values, rule}`: `copy_values` true means the codes are behaviour and synth may copy them. synth takes codes only from design-side sources (value lists, list validations, literals in criteria or code). When `copy_values` is true but the design holds no codes, `values` lists them: unique non-empty code strings that the client gave in answers.md (never taken from data), listed in rules.md for signing. Otherwise `values` is null; it is allowed only with `copy_values` true. With `copy_values` false synth generates codes.
- `relations[]` `{table, fk, on_delete CASCADE|PROTECT|SET_NULL|DO_NOTHING, rule}`; the relationship must exist in schema.json. `workflows[]` `{id snake_case, title, forms, reports, rules, steps}`. `services[]` `{name, area, inputs, outputs, errors}`, each also described in `interfaces.md`. `open_questions[]` `{id Q-<area>-NNN, text, status open|answered}`.
- `signed` is null in every committed file. The signature is recorded in `{{p.records}}/gates.json` S1 (§15), because a file cannot hold its own hash. The object form `{by, date, rules_md_sha256, rules_json_sha256}` is only for a copy handed to the client, where `rules_json_sha256` is the sha256 of the committed file.
- Everything in `scope.must_keep` is kept, and `keep-drop.md` names its component.

Coverage (`rules-coverage <rules.json> {{p.private}}/out/inventory.json --require 100`): the set is every inventory object whose type is in `rule_bearing_kinds`, plus every `table`. An object of the set is covered when it appears exactly once in `objects[]` and, if it is kept and rule-bearing, at least one rule cites it in `sources` or its `reason` says why it holds no rule. Coverage = covered objects / size of the set × 100, printed with one decimal, with the uncovered names (names only). Exit 1 below `--require`, when an object is listed twice, or when any `objects[].name` or `rules[].sources[].ref` names no inventory object (whatever the percentage). In the example above, `qryOrderTotals` is covered through R-orders-002 and `qryOpenPOs` through its reason (the example's `objects[]` is shortened: a real file also lists the areas' other tables, forms, reports and procedures).

`keep-drop.md` is generated by `rules-render <rules.json> --keep-drop <md>`: a first line saying it is generated from analysis/rules.json, then one table each for Keep, Drop and Out of scope (type, name, area, target component or reason), sorted by area, type and name. Never hand-edited. `--check` exits 1 on any difference.

`rules.md` (sign-off format) contains every `rules[].id` and every `open_questions[].id`, and ends with the sign-off block. Its sha256 is recorded at the S1 gate.

Tests: `test_rules.py` in each area holds one test per rule whose `test_kind` is `rule` (§14). Rules with `equivalence` are proven by units (§11), rules with `e2e` by S5.e2e.

**Fan-out items.** `pipeline.json` gives each fan-out that reads rules.json an `items_from` `{file, jq}`; the dry run returns it with `cmd`, the jq call in the mp-run form with the stage's prepare slot (§1 Command forms), and the orchestrator runs that `cmd` as returned to get `args.items["<fan-out id>"]` (jq 1.8.1, `pipeline.json` `stack.jq`, installed by the `jq` environment item). S1.tooling's test_rules.py runs both programs on its fixture and checks that the S5 program equals the one printed here.

**S1 areas** (`S1.areas`, on S1 reruns: every area, kept and dropped, so a rerun keeps the split; on the first S1 run the file does not exist and the scout picks the areas):
```
jq -c '. as $r | [.areas[] | {id, title, scope: (.id as $a | [$r.objects[] | select(.area == $a) | .type + ": " + .name] | join(", "))}]' {{p.target}}/analysis/rules.json
```
On the example above it prints:
```json
[{"id":"orders","title":"Orders and invoicing","scope":"table: Orders, query: qryOrderTotals, query: qryInvoice, query: qryOldReport"},{"id":"purchasing","title":"Purchasing","scope":"query: qryOpenPOs"},{"id":"admin_tools","title":"Admin tools","scope":"query: qryAudit"}]
```

**S5 items** (`S5.features`: kept areas only; tested on a sample rules.json):
```
jq -c '. as $r | [.areas[] | select(.status=="kept") | {id, title, scope: (.id as $a | ["queries: " + ([$r.objects[] | select(.type=="query" and .area==$a and .decision=="keep") | .name] | join(", ")), "rules: " + ([$r.rules[] | select(.area==$a) | .id] | join(", "))] | join("; "))}]' {{p.target}}/analysis/rules.json
```
On the example above it prints:
```json
[{"id":"orders","title":"Orders and invoicing","scope":"queries: qryOrderTotals, qryInvoice; rules: R-orders-001, R-orders-002"},{"id":"purchasing","title":"Purchasing","scope":"queries: qryOpenPOs; rules: "}]
```
- More than `max_items` (4) kept areas makes the fan-out blocked unless `allow_over_budget` is passed.

**`analysis/data-model.json` (xlsm only, S1.synthesize).** The schema.json table and relationship shapes (§4) with normalised snake_case names, plus the mapping from ListObject columns:
```json
{
  "format_version": 1,
  "source_kind": "xlsm",
  "tables": [
    {"name": "invoice", "role": "data", "columns": [{"name": "invoice_number", "type": "longinteger", "size": null, "precision": null, "scale": null, "required": true}, {"name": "customer_id", "type": "longinteger", "size": null, "precision": null, "scale": null, "required": true}], "primary_key": ["invoice_number"], "indexes": []},
    {"name": "invoice_line", "role": "data", "columns": [{"name": "invoice_number", "type": "longinteger", "size": null, "precision": null, "scale": null, "required": true}, {"name": "line", "type": "integer", "size": null, "precision": null, "scale": null, "required": true}, {"name": "service", "type": "text", "size": 255, "precision": null, "scale": null, "required": false}], "primary_key": ["invoice_number", "line"], "indexes": []}
  ],
  "relationships": [{"name": "invoice_lines", "from_table": "invoice_line", "from_columns": ["invoice_number"], "to_table": "invoice", "to_columns": ["invoice_number"], "enforce": true, "cascade_update": false, "cascade_delete": true}],
  "mappings": [
    {"list_object": "InvoiceTable", "source_column": "Invoice number", "table": "invoice", "column": "invoice_number", "line": null},
    {"list_object": "InvoiceTable", "source_column": "Services Line 1", "table": "invoice_line", "column": "service", "line": 1}
  ],
  "computed": [{"list_object": "InvoiceTable", "source_column": "Net Amount", "rule": "R-invoices-003"}],
  "dropped_tables": [{"list_object": "VBAGlossary", "reason": "documentation"}]
}
```
- InvoiceGenerator: Services/Date/Amount Line 1..3 become `invoice_line` rows (`line` 1..3); the customer columns copied into invoices become a foreign key to the customer table; the 6 calculated columns are `computed`; VBAGlossary is dropped.
- `schema-lint <data-model.json>`: jsonschema, then snake_case unique names, neutral types (§4), keys and relationship columns exist, every mapping target exists, no source column both mapped and computed. Exit 1 on any failure.
- stage1 reads it with `--schema-from data-model` (`auto` picks it for xlsm; S4.models adds this, §18).

Failure: `rules-lint` exits 1 and names the JSON pointer of each error. Besides the jsonschema and the field rules above it fails when: an id is duplicated; a rule's `<area>` differs from its `area` or names no area; an area id is a Python keyword or in `item_ids.reserved`; a cited question or rule id does not exist; a kept area has no kept object or a dropped area has one; an object (type, name) appears twice; a `calculated_column` name does not match `<Table>[<Column>]`, or a `formula_block` or `data_validation` name does not match `<Sheet>!<A1 range>` (in `objects[]` and `sources`); a rule with `test_kind` `e2e` is in no `workflows[].rules`; `lookups[].values` is set without `copy_values` true; an area file holds more than its one area; `signed` is not null. `rules-coverage`, `rules-render --check` and `schema-lint` exit 1 as above; any missing file exits 2.

---

<a id="reference-catalog"></a>
## 9. reference-catalog

- **Paths (zones as in §1):** catalog `{{p.private}}/out/reference/catalog.json` and `out/reference/sql/<file-safe id>.sql` (xlsm: `.txt` with rewritten formulas), both design zone; the engagement reference database `{{p.private}}/reference/engagement.accdb` (xlsm `.xlsx`), data zone; seed reference databases `{{p.private}}/synthetic/reference/seed-N.accdb` (xlsm `.xlsx`), design zone.
- **Producers:** S2.reference (Access kinds) and S2.reference_xlsm (xlsm), sharing `toolkit/commands/reference.py`, which dispatches on `source.kind` (and `ReferenceAdapter` on the catalog `engine`).
  - `uv run toolkit reference sql <eng> --all`: reads only the design zone, writes only `out/reference/`.
  - `uv run toolkit reference build <eng> --dataset seeds|seed-N`: reads only the design zone and the committed seeds (it runs the `sql` step first when the catalog is missing or older than the model), writes only `synthetic/reference/`.
  - `mp-data reference build <eng> --dataset engagement`: writes only `reference/engagement.accdb` (xlsm `.xlsx`), never the catalog or a seed database; it exits 2 naming `uv run toolkit reference sql <eng> --all` when the catalog is missing or older than the model. Every list that builds the engagement database runs the seeds build on the line before.
  - `--dataset all` exits 2 and names the two commands, because the jail (§3) lets a data-plane call write only the data zone and its declared outputs.
- **Consumers:** reference run and selfcheck, golden record (S2.golden), `equivalence --adapter reference`, S2.review.

```json
{
  "format_version": 1,
  "engine": "ucanaccess-5.1.8",
  "engagement": "northwind-demo",
  "units": [
    {"id": "qryOrderTotals", "kind": "saved_query", "object": {"type": "query", "name": "qryOrderTotals"}, "sql_ref": "sql/qryOrderTotals.sql", "depends_on": ["qryOrderLines"], "params": [], "app_functions": ["QtyOnOrder"], "status": "ok", "reason": null, "needs_access_confirmation": true},
    {"id": "qryByCustomer", "kind": "saved_query", "object": {"type": "query", "name": "qryByCustomer"}, "sql_ref": "sql/qryByCustomer.sql", "depends_on": [], "params": [{"name": "CustomerID", "type": "longinteger", "source": "form_control"}], "app_functions": [], "status": "needs-params", "reason": "form-reference-unresolved", "needs_access_confirmation": false}
  ]
}
```
- `engine`: `ucanaccess-5.1.8` or `formualizer-0.11.1`. `kind`: `saved_query`, `formula_output` or `document`.
- `object` `{type, name}`: the rules.json and inventory object the unit implements: a `query`; a `calculated_column` `<Table>[<Column>]`; a `formula_block` `<Sheet>!<A1 range>`; for a document, the `procedure` that writes the file.
- `sql_ref`: relative to `out/reference/`, file-safe id (Conventions).
- `depends_on`: queries the unit reads, in dependency order. `params[]`: `{name, type (neutral), source form_control|prompt|input_cell}` (`input_cell`: xlsm, for example the dashboard `as_of`, Dashboard!C1).
- `status`: `ok`, `needs-params`, `unsupported` or `empty`. `reference sql` writes the first three; `empty` (0 rows) is a per-dataset outcome, so tools record it in the manifest (§11), not here. `reason` is null for `ok`, else one of `action-query`, `crosstab`, `form-reference-unresolved`, `unsupported-function`, `pass-through`, `other`.
- `needs_access_confirmation` (bool): the SQL calls Format, FormatDateTime, DateValue, CDate, DatePart, DateDiff, DateAdd, Weekday, MonthName or an app function.
- Reference databases are built to a temp file in their target directory and renamed (a rename from `$TMPDIR` would cross mounts inside the jail, §3). Engagement: a private copy of `source.data_copy` (or of the source for kinds `accdb` and `mdb`), else built from `{{p.private}}/datasets/engagement`; xlsm: a copy of the workbook, whose cached values are the oracle. Seeds: built from `goldens/synthetic/seed-N/input` only. `reference run` opens them read-only.
- xlsm seed workbooks (`synthetic/reference/seed-N.xlsx`) are built with openpyxl from `{{p.private}}/out/model/` (sheets, ListObjects with ref, headers and calculated-column formulas, formula blocks with cell addresses, defined names, scrubbed label cells; §4) plus the committed seed rows, never copied from the shipped workbook (it holds engagement values, and agents may read `synthetic/`). A non-formula cell outside the tables that a formula reads is an input cell: a param of the units that read it (`source` `input_cell`), set at run time from `goldens/params.json`, never stored in the seed workbook.
- `ReferenceAdapter()` (no arguments, as the plugin creates it) finds the private directory in `$EQUIVALENCE_PRIVATE` (golden record may pass `private=`) and dispatches on the catalog `engine`.
- Engagement-specific app functions are rewritten as correlated subqueries in `{{p.target}}/goldens/reference/functions/<Function>.json` (paraphrased, committed, guard-checked). Only Java that other engagements can reuse goes in `pipeline:java/`: `start_jvm` (`toolkit.reference.bridge`, S2.reference) compiles it with javac into a jar under the process `TMPDIR` at each JVM start, so no task owns a `vendor/app-functions.jar` and `vendor/` holds only the fetched jars (the toolkit root is read-only in the jail, §3, and in a pinned release).
```json
{"function": "QtyOnOrder", "args": ["product_id"], "rewrite": "(SELECT SUM(Quantity) FROM PurchaseLines WHERE ProductID = {product_id} AND Received = 0)", "rule": "R-inventory-004", "choice": "subquery"}
```
- Summaries list unit ids with status and reason (§17 `units`; `reference run` adds `dataset` and `total`, the row count, with status `empty` for 0 rows), and `reference selfcheck` per-unit `matched` and `total` plus `rates.match`; never values.

Failure: `reference build` exits 1 with `empty reference` when every engagement table has 0 rows, and `--dataset engagement` exits 2 with `no engagement data: set source.data_copy` when an `accdt` or `vcs-export` source carries no data and `data_copy` is null (the seeds build as usual). `reference run` exits 1 when a unit fails at run time (Java exception class only, no values). `reference selfcheck --min-match 1.0` (xlsm) exits 1 below the ratio; InvoiceGenerator must reach 335 of 335. Missing `JAVA_HOME` or a jar exits 2 naming the environment item or `toolkit fetch --jars`.

---

<a id="canonical-output"></a>
## 10. canonical-output

- **Implementation:** `pipeline:harness/canonical.py` (stdlib only, vendored), owned by S2.golden; prose `docs/formats/canonical-output.md`. `canonical_version 1`.
- **Consumers:** golden record (S2.golden), the harness compare step (S2.harness and the vendored copy), report.

```
OrderID,Total,Shipped,OrderDate
1001,125.5000,1,2024-03-01T00:00:00
1002,\N,0,2024-03-02T09:30:00
```
Values, by the unit's neutral column types (manifest `columns`):
- CSV as in §7 (UTF-8, RFC 4180, CRLF, header = output column names, NULL `\N`, same backslash rule).
- Integers plain, no leading zeros, `-0` becomes `0`. `currency` and `decimal`: exactly 4 decimal places, half-even rounding.
- `single`: rounded half-even to 6 significant digits; `double` to 12; printed in plain notation without exponent, trailing zeros and a trailing `.` removed.
- `datetime` `YYYY-MM-DDTHH:MM:SS`, no zone (a time-only Access value keeps its 1899-12-30 date). `yesno` `1`/`0` (Access -1 is 1). Text NFC, otherwise unchanged.

Row order:
- The unit has an ORDER BY on output columns (manifest `order_by`): sort by those columns with a typed compare (NULL first, numbers numerically, datetimes chronologically, text by code point of its NFC form; `desc` reverses the column, NULL position included), then break ties by the full canonical row tuple.
- Otherwise sort by the full canonical row tuple (field strings compared as UTF-8 bytes).
- Ordering is normalised, not checked. A rule that depends on visible order gets a rule test or an e2e test.

Documents (§16): canonical JSON, one object per key, every leaf value a canonical string as above, `json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))` plus a newline.
```json
{"15001":{"customer":"Example Co","total":"118.0000"}}
```

Digest: `sha256` hex over the canonical bytes; for the engagement dataset `hmac-sha256` with the key from §11. `digest(data, key=None)` in canonical.py does both.

Failure: a value that does not fit its declared type raises an error, which the harness reports as a test error (never a silent cast).

---

<a id="goldens-manifest"></a>
## 11. goldens-manifest

- **Paths:** `{{p.target}}/goldens/manifest.json` and `{{p.target}}/goldens/params.json` (committed, no values); seed outputs `{{p.target}}/goldens/synthetic/seed-N/expected/<file-safe id>.csv` (`.json` for documents); engagement outputs `{{p.private}}/goldens/engagement/<file-safe id>.csv|json` and engagement parameter values `{{p.private}}/goldens/params.json` (data zone).
- **Producer:** S2.golden. `mp-data golden record <eng> --dataset all` (engagement and seeds; in the jail the child may write only `{{p.target}}/goldens/`, the data zone and `$TMPDIR`, §3); `uv run toolkit golden record <eng> --dataset seeds` (seeds only, design zone); `mp-data golden record <eng> --dataset engagement --private-only --check` in later `prepare` lists: it first writes `{{p.private}}/goldens/` (outputs and engagement params), then exits 1 when any engagement status, row count or digest differs from the committed manifest.
- **Consumers:** the harness runner (S2.harness, vendored), `golden status`, S4.data (structure only), S5.features (unit ids), S6.report (statuses and reasons; it never reads the catalog).

```json
{
  "format_version": 1,
  "engagement": "northwind-demo",
  "units": {
    "qryOrderTotals": {
      "area": "orders",
      "kind": "saved_query",
      "canonical_version": 1,
      "columns": [{"name": "OrderID", "type": "longinteger"}, {"name": "Total", "type": "currency"}],
      "order_by": [{"column": "OrderID", "desc": false}],
      "needs_access_confirmation": true,
      "datasets": {
        "engagement": {"status": "ok", "rows": 48, "reason": null, "digest": {"alg": "hmac-sha256", "value": "cd0aa9856147b6c5b4ff2b7dfee5da20aa38253099ef1b4a64aced233c9afe29"}},
        "seed-1": {"status": "ok", "rows": 50, "reason": null, "digest": {"alg": "sha256", "value": "aaa9402664f1a41f40ebbc52c9993eb66aeb366602958fdfaa283b71e64db123"}, "expected": "synthetic/seed-1/expected/qryOrderTotals.csv"},
        "seed-2": {"status": "needs-params", "rows": null, "reason": "form-reference-unresolved", "digest": null}
      }
    }
  }
}
```
- Units are keyed by catalog id. The manifest holds only units whose rules.json object has decision `keep` (Q, §13); units of dropped or out-of-scope objects get no entry, so the harness never selects them. `area` comes from the rules.json object the unit names; a unit whose object is missing from rules.json is recorded with `area` null, which `golden status` rejects.
- Per dataset: `status` `ok`, `needs-params`, `unsupported` or `empty` (0 rows on that dataset); `rows` and `digest` for `ok` and `empty`, null otherwise; `reason` null for `ok`, `empty` for `empty`, the catalog reason when the catalog status is not ok (`action-query`, `crosstab`, `form-reference-unresolved`, `unsupported-function`, `pass-through`, `other`), and `other` for `needs-params` when no workable parameter value was found on that dataset; `expected` (seeds only) relative to `goldens/`. S6.report takes statuses and reasons only from here.
- Engagement digest key = HMAC-SHA256(key = `b"modpipe-golden-v1"`, message = the bytes of the engagement data file: `source.data_copy` when set, else `source.path`). When that is a folder (`vcs-export`), the bytes of `{{p.private}}/datasets/engagement/dataset.json` are used instead (it lists every table's sha256). Anyone with the data file can re-verify; nobody else can enumerate small outputs. The toolkit computes the key in memory, passes it to the harness in `EQUIVALENCE_DIGEST_KEY` and never prints or writes it.
- `order_by` lists the output columns the unit's ORDER BY uses, so recorder and comparer order rows the same way (§10); null when the unit has no ORDER BY on output columns. Document units have `columns` null and `rows` = number of keys.
- `needs_access_confirmation` (the needs-access-confirmation flag, copied from the catalog) marks units to review at the S2 gate, where you decide on an optional Windows + Access confirmation run.

```json
{"format_version": 1, "units": {"qryByCustomer": {"params": [{"name": "CustomerID", "type": "longinteger", "source": "form_control"}], "values": {"seed-1": {"CustomerID": "3"}, "seed-2": {"CustomerID": "7"}, "seed-3": {"CustomerID": "1"}}, "keys": {}}, "invoice_pdf": {"params": [], "values": {}, "keys": {"seed-1": ["10001", "10002"]}}}}
```
```json
{"format_version": 1, "units": {"qryByCustomer": {"values": {"engagement": {"CustomerID": "<chosen by the tool>"}}, "keys": {}}}}
```
- The committed `params.json` holds definitions and synthetic values only; engagement values and document keys go only to `{{p.private}}/goldens/params.json`. Values are canonical strings (§10).
- Values are picked by the tool: a key-like parameter takes the smallest key that gives a non-empty result (at most 20 tries); a date parameter the latest date in the filtered column; an xlsm input cell the shipped value for the engagement, and for seeds the seed's latest invoice date for a date input (dashboard `as_of`) or the value a rule in answers.md gives for any other input. A choice rule in answers.md overrides this. No workable value gives `needs-params` with reason `other`.

Failure:
- `golden record --check` exits 1 when a re-record differs from the committed manifest, params or expected files (or the private goldens when present); it never writes, except with `--private-only`: `--dataset engagement --private-only --check` first writes `{{p.private}}/goldens/` (outputs and engagement params), then exits 1 when any engagement status, row count or digest differs from the committed manifest.
- `--private-only` writes only `{{p.private}}/goldens/`, never a committed file, and exits 1 when any engagement status, row count or digest differs from the committed manifest.
- `golden status` (§13) exits 1 below `--min-ok`, above `--max-empty`, on an unknown `area`, or when a kept query (xlsm: a kept calculated column or formula block) has no unit.
- A missing reference database exits 2 naming the command that builds it (`uv run toolkit reference build <eng> --dataset seeds` or `mp-data reference build <eng> --dataset engagement`); a missing catalog names `uv run toolkit reference sql <eng> --all`.

---

<a id="harness"></a>
## 12. harness

- **Code:** `pipeline:harness/` (S2.harness; `canonical.py` from S2.golden, `dataset.py` from S0.dataset), vendored into `{{p.target}}/equivalence/` by `uv run toolkit vendor-harness --target {{p.target}}`, which leaves `adapter.toml` and `results.json` alone. The vendored copy never imports toolkit; modules import each other relatively, so it works as `harness` and as `equivalence`. Files: `plugin.py`, `adapter.py`, `compare.py`, `redact.py`, `README.md` (how the client re-runs it), `tests/test_units.py` (the runner) and the harness's own `tests/test_*.py`, which the plugin ignores during cell runs.
- **CLI:** `toolkit equivalence` (S2.harness).
- **Producers of adapter config:** S4.data (`{{p.target}}/equivalence/adapter.toml`, `<slug>.core.equivalence:DjangoAdapter`).
- **Consumers:** S2, S4, S5 and S6 acceptance and exit criteria; target CI (synthetic seeds only); S6.report (JUnit, `results.json`).

<a id="adapter-protocol"></a>
### Adapter protocol
Duck-typed; the plugin instantiates the entrypoint class with no arguments.

| Method | Contract |
|---|---|
| `load_dataset(dir)` | Load the dataset directory (§7). The Django adapter flushes the legacy tables first and loads inside the test database. |
| `run_saved_query(unit_id, params) -> rows` | Row tuples in the manifest's column order; Python values (int, Decimal, float, str, bool, datetime, None). `params` maps names to canonical strings. Serves `saved_query` and `formula_output` units. |
| `render_document(unit_id, key, params) -> dict` | The canonical fields of §16 (`document` units). |
| any unit nobody registered | raise `NotImplementedError`; the harness defines `equivalence.adapter.NotImplementedUnit` as a subclass, and the Django adapter raises it with `not implemented: <unit id>`. Outcome `not-implemented`. |

The pipeline-only reference adapter (`toolkit.commands.reference:ReferenceAdapter`) adds `describe(unit_id) -> [(column, neutral type)]` for golden record; for Access kinds its `render_document` raises `NotImplementedError`.

`adapter.toml`:
```toml
format_version = 1
adapter = "django"
project_dir = "../app"
python = ["uv", "run", "--project", "../app", "python"]
settings = "config.settings.test"
entrypoint = "northwind.core.equivalence:DjangoAdapter"

[databases]
postgresql = "PG_URL"
mssql = "MSSQL_URL"
```
`settings` stays `config.settings.test`: the vendored runner does not load `app/conftest.py` (`../equivalence/tests` is outside the app rootdir), so the test-database naming lives in `config/settings/test.py` too (§14).

<a id="equivalence-cli"></a>
### toolkit equivalence
`toolkit equivalence <eng> --adapter reference|django --datasets synthetic|engagement|all|<list> [--dbs postgresql|mssql|from-engagement] [-k EXPR] [--only-areas A[,B]] [--collect-only] [--allow-not-implemented] [--allow-empty] [--min-pass-per-cell F] [--junit-dir DIR] [--matrix FILE]`
- `--datasets` (required, no default, so nothing touches the engagement data by accident): `synthetic` = every seed in the manifest; `all` = engagement plus seeds; `<list>` = comma-separated names such as `seed-1`.
- `--dbs` (required with `--adapter django`, refused with `--adapter reference`, exit 2): `from-engagement` = `database.primary` plus `database.verify_also`. The reference adapter's cell db is always `reference`.
- One pytest process per (dataset, db) cell. `--collect-only` passes `--collect-only` to every cell, prints the test ids and runs nothing.
- Selection per cell: only units whose golden for that dataset has status `ok` (and, with `--only-areas`, whose area is listed), then `-k` (passed to pytest unchanged) on the test ids `test_unit[<area>::<unit>::<dataset>::<db>]`. `-k '[<area>::'` selects one area exactly; `-k orders` would also match `purchase_orders::...`. The manifest holds kept units only, so a cell without `-k` and `--only-areas` selects exactly C_d (§13).
- A cell with nothing selected (pytest exit 5) is reported as `empty` (total 0, rate null) and does not fail the run. Exit 5 only when every cell is empty; with `--allow-empty` that case exits 0 with summary status `no-data`.
- Cell rate = passed / selected units, so with `-k` or `--only-areas` the denominator is the units the cell selected (§13).
- Exit 1 when:
  - `--min-pass-per-cell F` is given and any non-empty cell's rate is below F;
  - `--min-pass-per-cell` is not given and any selected test has an outcome other than passed or not-implemented;
  - `--allow-not-implemented` is not given and any not-implemented outcome occurs.
- `--allow-not-implemented` only stops not-implemented outcomes from failing the run; they still count as not passed.
- `--only-areas A[,B]` (each id must match the item id pattern, §1, else exit 2): every cell selects only units of those areas; a django cell also starts Django with only those areas installed (§14 `MP_ONLY_AREAS`). Used by S5 per-item runs; gate runs and exit criteria run with all areas.
- Runs that include the engagement dataset are data-plane calls (`mp-data equivalence`) and keep `--junit-dir {{p.private}}/results/<stage>` (`--matrix` beside it). Synthetic-only runs use `uv run toolkit equivalence` with `--junit-dir {{p.private}}/synthetic/results/<stage>`.
- Without `--junit-dir`, JUnit and obtained files go to a temp directory that is removed at exit, so a run without `--junit-dir` and `--matrix` writes nothing outside `{{p.private}}/logs/`.

Toolkit flags and the pytest-plugin options behind them (the target CI passes plugin options directly, without the toolkit):

| toolkit | plugin (`-p equivalence.plugin`) |
|---|---|
| per cell | `--eq-adapter <adapter.toml>`, `--eq-dataset <name>`, `--eq-db <db>` |
| `--allow-not-implemented` | `--eq-allow-not-implemented` |
| `--only-areas A,B` | `--eq-only-areas A,B` (sets `MP_ONLY_AREAS` before Django starts) |
| `-k EXPR` | `-k EXPR`, unchanged |
| `--allow-empty`, `--min-pass-per-cell`, `--matrix` | none: the toolkit applies them across cells |

Exec lines:
- reference: the pipeline's Python, cwd `{{p.target}}`, `-m pytest equivalence/tests -p equivalence.plugin --eq-adapter <temp adapter.toml> --eq-dataset <name> --eq-db reference --junitxml <file> -p no:cacheprovider`. The temp adapter.toml's entrypoint is `toolkit.commands.reference:ReferenceAdapter`.
- django: cwd `{{p.target}}/app`, `<python> -m pytest -c pyproject.toml --rootdir . ../equivalence/tests -p equivalence.plugin --create-db --eq-adapter ../equivalence/adapter.toml --eq-dataset <name> --eq-db <db> --junitxml <file> -p no:cacheprovider`. The app's `[tool.pytest.ini_options]` has `pythonpath = [".", ".."]`, so `equivalence` imports as a package.
- `<python>` is the adapter.toml `python` command, except inside mp-data (`MP_SANDBOX=mp-data`): there it is the app's own interpreter `{{p.target}}/app/.venv/bin/python`, because the jail (§3) allows no uv, which would lock or sync the read-only app environment. When that interpreter is missing, equivalence exits 2 naming `cd {{p.target}}/app && uv sync --locked`; the S5 and S6 prepare lists start with that line.

Environment of each cell (both adapters unless the row says django):

| Variable | Value |
|---|---|
| `DJANGO_SETTINGS_MODULE` | django: `settings` from adapter.toml |
| `DATABASE_URL` | django: the server URL + `/{{e.db_name}}`. The server URL is `MP_DATA_PG_URL` or `MP_DATA_MSSQL_URL` when set (only inside mp-data, where they hold the `mp_runner` URLs, so data-plane runs connect as `mp_runner`, which owns the test databases it creates; §1 Databases), else the variable named in `databases[<db>]` (`PG_URL` or `MSSQL_URL`). The toolkit always sets it. When it is unset (target CI runs the plugin without the toolkit), the plugin sets it, before pytest-django reads the settings, to the variable named in `databases[<--eq-db>]` + `/` + the app slug (the first dotted part of `entrypoint`), and stops with a usage error naming that variable when it is unset too. |
| `MP_DB_SUFFIX` | passed through; the test database is `<MP_TEST_DB_PREFIX>{{e.db_name}}_<MP_DB_SUFFIX>` (§14) |
| `MP_TEST_DB_PREFIX` | django: passed through; `mpdata_test_` inside mp-data, else unset (`test_`) |
| `MP_ONLY_AREAS` | django: set to the `--only-areas` list (the plugin sets it from `--eq-only-areas`) when given; without that option, an `MP_ONLY_AREAS` already in the environment passes through unchanged (S5's per-item acceptance sets it as a command prefix); unset means all areas |
| `EQUIVALENCE_PRIVATE` | `{{p.private}}`. It may name any directory (target CI: one under the runner's temp dir); the plugin reads only `datasets/engagement` and `goldens/params.json` under it, writes only `logs/`, and uses it only for the engagement dataset. The reference adapter finds its databases through it (§9). |
| `EQUIVALENCE_OBTAINED` | pytest-regressions obtained files: `{{p.private}}/synthetic/results/obtained/<dataset>-<db>/` when `--junit-dir` lies under `{{p.private}}/synthetic/` and the run is not inside mp-data (synthetic-only runs), else a temp dir removed at exit, so a data-plane run writes no design-zone file; unset in target CI (pytest's `tmp_path`) |
| `EQUIVALENCE_REDACT` | `1` for the engagement dataset (always; no opt-out) |
| `EQUIVALENCE_DIGEST_KEY` | the §11 key, engagement dataset only |
| `EQUIVALENCE_DATA_FILE` | optional, for a client re-run without the toolkit: when `EQUIVALENCE_DIGEST_KEY` is unset, the harness derives the §11 key from this file (stdlib `hmac`) |

A django cell on the engagement dataset exits 2 with `run through mp-data` unless `MP_TEST_DB_PREFIX` is `mpdata_test_` and the `MP_DATA_` URL for its db is set.

Runner:
- One test per selected manifest unit, id `test_unit[<area>::<unit>::<dataset>::<db>]`; a document unit is one test covering all its keys.
- Dataset dirs: `engagement` = `$EQUIVALENCE_PRIVATE/datasets/engagement`; `seed-N` = `goldens/synthetic/seed-N/input` next to `equivalence/`. `load_dataset` runs once per process. Params come from `goldens/params.json`, engagement values from `$EQUIVALENCE_PRIVATE/goldens/params.json`.
- Engagement compare: HMAC digest of the canonical bytes against the manifest. On a mismatch, datacompy against the private golden with every column (join keys included) passed to `hide_sensitive_columns`, `report(sample_count=0)`, written to `$EQUIVALENCE_PRIVATE/logs/`.
- Seed compare: pytest-regressions file check against `expected/<file-safe id>` with `obtained_filename` in `$EQUIVALENCE_OBTAINED` (pytest's `tmp_path` when it is unset). The session fails at start when `--force-regen` or `--regen-all` is set. pytest-regressions is never used for the engagement dataset.
- Redaction (`harness/redact.py`, always on for the engagement dataset): a hookwrapper on `pytest_runtest_makereport` writes longrepr and captured sections to `$EQUIVALENCE_PRIVATE/logs/`, then replaces longrepr with `<nodeid>: <n> rows differ in columns [...]` and empties the sections. A canary test with synthetic cells `CANARY-<n>` proves nothing leaks to stdout, stderr or JUnit.
- A missing golden for a listed dataset is an error, never a skip.

<a id="junit"></a>
### JUnit, matrix, results.json and known differences
JUnit file names: `junit-<suite>-<dataset>-<db>.xml`.
- `junit-equivalence-<dataset>-<db>.xml`: one per cell, empty cells included, written by `equivalence --junit-dir DIR`.
- `junit-app-fixtures-<db>.xml`: the app's own pytest run (rule tests, e2e and the rest), written by `pytest --junitxml` into `{{p.private}}/synthetic/results/<stage>/`. S6.report runs the whole app suite with e2e on PostgreSQL (`junit-app-fixtures-postgresql.xml`) and, when mssql is used, the suite with `--ignore=tests/e2e` on SQL Server (`junit-app-fixtures-mssql.xml`).
- Not-implemented is recorded as `<skipped type="equivalence.not-implemented" message="not-implemented: <unit>">`.

`--matrix FILE` (counts only):
```json
{"format_version": 1, "cells": [{"dataset": "seed-1", "db": "mssql", "status": "ok", "passed": 60, "failed": 1, "errors": 0, "skipped": 0, "not_implemented": 2, "total": 63, "rate": 0.9524}]}
```
`status` is `ok` or `empty`; an empty cell has `total` 0 and `rate` null. `results summarize` derives the required cells from the engagement and the manifest (below), never from a file in the JUnit directories.

`{{p.target}}/equivalence/results.json` (committed, counts and unit ids only), owned by S6.report and written by `mp-data results summarize <eng> --junit-dir {{p.private}}/results/<stage> --junit-dir {{p.private}}/synthetic/results/<stage> --out {{p.target}}/equivalence/results.json`:
```json
{
  "format_version": 1,
  "engagement": "northwind-demo",
  "date": "2026-10-16",
  "gate": {"min_cell_rate": 0.9365, "pooled_rate": 0.9657, "threshold": 0.9},
  "cells": [
    {"suite": "equivalence", "dataset": "engagement", "db": "postgresql", "passed": 61, "failed": 1, "errors": 0, "skipped": 0, "not_implemented": 1, "total": 63, "rate": 0.9683,
     "not_passed": [{"unit": "qryBackorders", "outcome": "failed"}, {"unit": "qryStockTake", "outcome": "not_implemented"}]},
    {"suite": "rules", "dataset": "fixtures", "db": "postgresql", "passed": 42, "failed": 0, "errors": 0, "skipped": 0, "not_implemented": 0, "total": 42, "rate": 1.0},
    {"suite": "e2e", "dataset": "seed-1", "db": "postgresql", "passed": 3, "failed": 0, "errors": 0, "skipped": 0, "not_implemented": 0, "total": 3, "rate": 1.0}
  ]
}
```
- Required files: `junit-equivalence-<dataset>-<db>.xml` for every dataset in {`engagement`, each seed in `{{p.target}}/goldens/manifest.json`} × every db in `database.primary` plus `database.verify_also`; `junit-app-fixtures-postgresql.xml` always; `junit-app-fixtures-mssql.xml` when mssql is in `database.primary` or `database.verify_also`. They are looked up in the `--junit-dir` directories, not recursively. A required file found in no directory exits 1 naming it; one found in two directories (so a stale file in `results/S6` cannot hide the current one in `synthetic/results/S6`), or malformed XML, exits 2. Files for other cells are ignored and named in the summary `messages`.
- Counts per cell: passed, failed, errors, skipped (pytest records xfail as skipped), not_implemented (skipped with type `equivalence.not-implemented`), total (test cases); rate = passed / total, 0 when total is 0. Only equivalence cells carry `not_passed` (`outcome` `failed`, `error`, `skipped` or `not_implemented`).
- The app JUnit is split into `rules` (test cases from `*/tests/test_rules.py`, dataset `fixtures`), `e2e` (test cases under `app/tests/e2e/`, dataset `seed-1`) and other tests (counted in neither). A split with no test cases gives no cell, so the SQL Server app file gives a `rules` cell only. `report` exits 1 when results.json has no `rules` cell or no `e2e` cell.
- `gate` covers the equivalence cells: `min_cell_rate`, `pooled_rate` (§13) and `threshold` 0.9.
- `date` is the latest testsuite timestamp in the inputs, so two runs on the same files give the same bytes. Failure messages, system-out, system-err and properties are never copied.
- SQL Server cells come from local runs; CI JUnit artifacts are a cross-check only.

`{{p.target}}/docs/known-differences.json` (committed, written by the S6.report agent, read by `report`):
```json
{"format_version": 1, "entries": [{"unit": "qryBackorders", "cells": ["engagement:postgresql"], "reason": "the source sorts open orders by a form filter that the app replaces with an explicit filter; counts differ, see R-orders-007", "rule_ids": ["R-orders-007"]}]}
```
- One entry per unit that did not pass in some cell; `cells` as `<dataset>:<db>`; `reason` in words, never values; an empty `entries` list when everything passed. `report` exits 1 when a not-passed unit and cell has no entry.

Failure:
- `equivalence` exits 5 when every cell is empty (without `--allow-empty`), 1 as listed above, 2 on a missing manifest, adapter.toml, dataset dir or env variable, on `--dbs` with the reference adapter or its absence with the django adapter. Any cell pytest exit other than 0, 1 or 5 counts as 2.
- `vendor-harness --check` exits 1 on drift.

---

<a id="pass-rate"></a>
## 13. pass-rate

- **Computed by:** `golden status` (S2.golden), `equivalence --min-pass-per-cell` (S2.harness), `results summarize` and `report` (S6.report).
- **Used by:** S2, S5 and S6 exit criteria and gates; docs/proposal.md §2.2 success criteria.

Definitions:
- **Q** = manifest units whose rules.json object has decision `keep` (kept units). Every kept query (xlsm: calculated column or formula block) must have a unit.
- **C_d** = units of Q with status `ok` on dataset d.
- **Cells** = datasets {`engagement`, `seed-1`, `seed-2`, `seed-3`} × dbs (`from-engagement`: primary plus verify_also). The demos have 4 × 2 = 8 cells.
- **Cell rate** = passed / |C_d|. Failed, error, skipped, xfail, xpass and not-implemented all count as not passed.
- **Gate** = the minimum over cells ≥ 0.90 (`--min-pass-per-cell 0.9` in the S5 and S6 exit criteria). Gate runs never use `-k`, `--only-areas`, `--collect-only`, `--allow-empty` or `--allow-not-implemented` (so any not-implemented unit also fails them, §12). The S2 reference run must reach 1.0 in every cell.
- An empty cell (|C_d| = 0) does not fail `equivalence`: there it has rate null and `--min-pass-per-cell` skips it. `results summarize` and `report` give it rate 0 (total 0), so the S6 gate fails on it; `golden status` catches it earlier on the engagement dataset.
- **Per-item runs** (S5.features, with `-k '[<area>::'`, `--allow-empty` and, on the data-plane line, `--only-areas <area>`): each cell's denominator is the selected units; a cell with none selected is empty and skipped. These are progress checks, not the gate.
- The report also shows the pooled rate Σ passed / Σ |C_d| and the coverage-adjusted rate Σ passed / (|Q| × number of cells).
- Rule tests and e2e tests must pass 100% (any failure fails the app pytest exit criterion).
- **Characterization rate** (S2 gate) = |C_engagement| / |Q| ≥ 0.90, with empty units on the engagement dataset ≤ 25% of |Q| (`golden status ... --min-ok 0.9 --max-empty 0.25`).

Summary line of a gate run:
```json
{"status":"ok","command":"equivalence","engagement":"northwind-demo","exit_code":0,"counts":{"cells":8,"kept_units":63,"passed":478,"tests":495},"rates":{"min_cell":0.9365,"pooled":0.9657,"coverage_adjusted":0.9484},"cells":[{"dataset":"engagement","db":"mssql","status":"ok","passed":59,"failed":3,"errors":0,"skipped":0,"not_implemented":1,"total":63,"rate":0.9365}],"messages":[],"log":"$HOME/engagements/northwind-demo/private/logs/20261016T120000Z-s5_integrate-48211-equivalence.log"}
```
(`cells` shortened to one entry here; a real summary lists all 8.)

---

<a id="feature-app-contract"></a>
<a id="feature-app"></a>
## 14. feature-app-contract

- **Built by:** S4.template (overlay discovery, `config/`, test-database naming, database settings); S4.models (`legacy/`); S4.data (`core/`: registries, adapter, import, fixtures, roles).
- **Used by:** S5.shared, S5.features (one item each), S5.e2e, S6.

Layout of one feature area (owned by its S5 item, `target:app/{{e.app.slug}}/{{item.id}}/`):
```
app/<slug>/<area>/
  __init__.py
  apps.py            AppConfig: name "<slug>.<area>", label "<area>", feature = True, nav_label "<Title>"
  urls.py            app_name = "<area>"; form_<file-safe id> and report_<file-safe id> URLs
  saved_queries.py   @saved_query("<unit id>") and @document("<unit id>") functions
  services.py        the area's services from analysis/interfaces.md
  views.py           (or views/) tables2, filter, crispy forms, HTMX partials
  jobs.py            JOBS, only when a rule of the area has a schedule trigger
  management/commands/<job>.py   one per JOBS entry (with management/__init__.py and management/commands/__init__.py)
  templates/<area>/
  static/<area>/vendor/   vendored third-party files, if any
  THIRD_PARTY.md     only when the area vendors something
  reports/           WeasyPrint templates and Excel exports
  tests/__init__.py
  tests/test_rules.py  one test per rule of the area with test_kind rule; the docstring starts with the rule id
  tests/test_views.py  one parametrize table over the area's kept forms and reports
```
- `urls.py` names a URL `form_<file-safe id>` for every rules.json object of this area with type form and decision keep, and `report_<file-safe id>` for every kept report; a report view returns an A4 PDF with `?format=pdf`. `tests/test_views.py` lists each such object name in a parametrize table and checks the anonymous redirect, the 403 for a user without the role and the 200 (a PDF body starts with `%PDF-`).
- A rule with a schedule trigger (§8) becomes `app/<slug>/<area>/management/commands/<job>.py`, listed in `<area>/jobs.py` as `JOBS = [{"command", "schedule" (cron syntax), "rule"}]`, with a rule test that calls `call_command`; S6.handover documents every JOBS entry in `docs/runbook.md`.
```python
class OrdersConfig(AppConfig):
    name = "northwind.orders"
    label = "orders"
    feature = True
    nav_label = "Orders"
```

Overlay behaviour (S4.template):
- `LOCAL_APPS` = `<slug>.users` plus every other `<slug>/*/apps.py` package (sorted), found when settings load (`config/discovery.py` `discover_local_apps`).
- `MP_ONLY_AREAS` (comma-separated area ids): when set and not empty, discovery returns only `core`, `legacy` and `domain` (those that exist) plus the named areas, chosen by directory name without importing any other area's `apps.py`; `<slug>.users` stays installed. An id without a package raises ImproperlyConfigured. Unset or empty means every area. S5 per-item commands that start Django set it (`MP_ONLY_AREAS={{item.id}} uv run ...`); the data-plane line passes `--only-areas {{item.id}}` instead (§12). This removes the race where a half-written area breaks `django.setup()` for the other items.
- `config/urls.py` includes `<slug>.<area>.urls` at `/<area>/` for every AppConfig with `feature = True` that has `urls.py`.
- `core.apps.CoreConfig.ready()` calls `autodiscover_modules("saved_queries")`, which imports installed apps only.
- `base.html` builds the nav from feature AppConfigs (`nav_label`, `/<label>/`), sorted.
- `core`, `legacy` and `domain` AppConfigs have no `feature` flag. The domain app (S5.shared: functions and querysets used by more than one area) is installed with no URL and no nav entry.
- Database: a `DATABASE_URL` already in the environment wins over `app/.env` (read with overwrite off). Without either, the app uses `$PG_URL/{{e.db_name}}`, so a fresh checkout without the gitignored `app/.env` still works. `DATABASE_URL="$MSSQL_URL/<db>"` works for `manage.py` and `pytest --create-db`: mssql URLs get OPTIONS `python_driver` `mssql_python` and, for host 127.0.0.1 or localhost, `extra_params` `TrustServerCertificate=yes`.
- `app/.env` (gitignored) holds `DATABASE_URL=$PG_URL/{{e.db_name}}`, resolved when new-app runs. The app dev database holds synthetic data only.
- Test databases: one helper, `config/testdb.py`, used by `config/settings/test.py` and by the `django_db_modify_db_settings` fixture in `app/conftest.py`, names them `<MP_TEST_DB_PREFIX><NAME>_<MP_DB_SUFFIX>`, or `<MP_TEST_DB_PREFIX><NAME>` when `MP_DB_SUFFIX` is empty (prefix default `test_`; mp-data sets `mpdata_test_`, §1 Databases). A name over 63 characters becomes its first 54 characters, `_` and 8 hex characters of its sha1; with a db_name of at most 41 characters the cut keeps the prefix, the db_name and the `_` after it, so purge's patterns still match. pytest addopts have no `--reuse-db`; commands pass `--create-db`.
- `app/conftest.py` sets `pytest_plugins = ["<slug>.core.testing"]` when that module exists, so tests under `app/tests/e2e/` reach its fixtures.
- Target CI (`target-repo:.github/workflows/{{e.app.slug}}.yml`, S4.template; no toolkit, no secrets, no engagement data): the app tests run with `--ignore=tests/e2e` on each database; e2e runs in its own job, where every step after checkout is guarded by a step-level `if: hashFiles('<target.root>/app/tests/e2e/test_*.py') != ''` (a job-level `if` cannot use hashFiles), so the job passes with nothing to run until S5.e2e adds tests. CI sets no `E2E_SCREENSHOT_DIR` or `E2E_VIDEO_DIR` (those point under `{{p.private}}/synthetic/media/` only in local runs), and no `MP_ONLY_AREAS`, `MP_DB_SUFFIX` or `MP_TEST_DB_PREFIX`. The harness job runs the §12 exec line on the seeds with `--eq-allow-not-implemented` and without `--eq-only-areas`, sets `PG_URL` and `MSSQL_URL` but no `DATABASE_URL`: the vendored plugin sets it (§12 `DATABASE_URL` row), so the test database is `test_<slug>`.

`core/` and `legacy/` (S4.data, S4.models):
- `core/saved_queries.py`: the `@saved_query("<unit id>")` registry (saved_query and formula_output units) and, beside it, the `@document("<unit id>")` registry that `DjangoAdapter.render_document` dispatches to.
- `core/equivalence.py`: `DjangoAdapter` (§12).
- `core/testing.py`: pytest fixtures `load_seed(n)` (loads `goldens/synthetic/seed-<n>/input` into the test database) and `user_with_role(role)`.
- `manage.py import_dataset DIR [--flush]`: the dataset directory (§7) is its only positional argument; it loads through `legacy/resources.py` and, when present, `legacy/import_map.py`; prints per-table counts only. It refuses (exit 2, naming the database but no value) a dataset whose `dataset.json` kind is `engagement` on a local database (host 127.0.0.1 or localhost) whose name does not start with `mpdata_` or whose owner (PostgreSQL `pg_database.datdba`, SQL Server `sys.databases.owner_sid`) is not `postgres`, `sa` or `mp_runner`, the rule stage1 applies (§1 Databases). A data-plane equivalence run passes, because Django creates its test database through mp-data's `mp_runner` URL.
- `manage.py create_demo_users`: one `is_staff` user per role (`<role>@example.com`) plus `admin@example.com`; password from `DEMO_PASSWORD` in the environment (exit 1 when unset; never stored); a verified primary EmailAddress for each; prints the emails, never the password.
- Engagement-specific code lives in `core/local/`; `ROLES` is in `core/local/roles.py`. The rest of `core/` comes from `pipeline:templates/django-overlay/core/` through `new-app --only core`.
- `legacy/resources.py`: `RESOURCES`, a dict from `db_table` to its ModelResource, whose fields use `column_name` = the original column name. `legacy/import_map.py` (xlsm only): `IMPORT_MAP`, a dict from ListObject name to a function that turns one dataset row into `(db_table, field dict)` pairs, following `analysis/data-model.json`.

Vendored third-party files:
- Pinned in `pipeline.json` `stack.app_assets`. InvoiceGenerator's dashboard uses `chartjs`: Chart.js 4.5.1, file `chart.umd.min.js`, URL `https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js`, licence MIT.
- The S5 task that vendors an asset downloads exactly that file into `app/<slug>/<area>/static/<area>/vendor/` and writes the area's `THIRD_PARTY.md` with name, version, URL, the sha256 of the downloaded file and licence. S6.handover compiles every `THIRD_PARTY.md` (including `core/THIRD_PARTY.md`, Jackcess, Apache-2.0) into NOTICE.md.

Rules for feature agents:
- Never edit `config/`, `core/`, `legacy/`, `domain/`, `users/`, `templates/base.html` or another area. Shared logic used by more than one area lives in `app/<slug>/domain/` (S5.shared).
- `saved_queries.py` registers exactly the manifest unit ids of the area's kept units; unregistered units stay not-implemented.
- App code computes from the database; never reads goldens, never branches on dataset or seed.
- Acceptance per item (synthetic data only):
  - `cd {{p.target}}/app && MP_ONLY_AREAS={{item.id}} uv run pytest -q --create-db {{e.app.slug}}/{{item.id}}`
  - `MP_ONLY_AREAS={{item.id}} uv run toolkit equivalence {{p.records}}/engagement.json --adapter django --datasets synthetic --dbs from-engagement -k '[{{item.id}}::' --min-pass-per-cell 0.9 --allow-empty`
- Verify per item adds the data-plane line `mp-data equivalence {{p.records}}/engagement.json --adapter django --datasets all --dbs from-engagement -k '[{{item.id}}::' --only-areas {{item.id}} --min-pass-per-cell 0.9 --allow-empty`.

`app/.overlay-manifest.json` (written by `new-app`, never edited; `--check` compares these files, and for `pyproject.toml` and `uv.lock` only that every pin is present, so dependency requests that add packages keep it green):
```json
{"format_version": 1, "template_commit": "47fa6ff75639865c36c797cb790b30e5b3a57a42", "files": {"config/settings/base.py": "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb", "config/urls.py": "3e23e8160039594a33894f6564e1b1348bbd7a0088d42c4acb73eeaed59c009d"}}
```

Failure: `new-app --check` exits 1 listing the differing paths. A feature package that breaks discovery (no `apps.py`, wrong label) fails `manage.py check` and the area's pytest run.

---

<a id="records"></a>
## 15. records

Everything under `{{p.records}}`. For demos it is in the pipeline repo; for clients it is a local git repo (`git init` before S0) that integrate commits separately and pushes only when it has a remote.

| File | Producer | Consumers |
|---|---|---|
| `engagement.json` | you (§2) | everything |
| `paths.local.json` (gitignored) | orchestrator (§1) | loader, mp-data, mp-run |
| `source.lock.json` | fetch (§6) | fetch, loader |
| `inventory.lock.json` | inventory (§5) | inventory `--expect` |
| `log.md` | header S0.workspace; rows integrate/checkpoint; minutes and tokens the orchestrator | docs/runbook.md section 3 budget check and Recalibration block (by hand; no toolkit command changes estimate.toml) |
| `runs/<stage>.json` (a change-order run: `runs/S1-CO-NNN.json`, so the assessment report stays) | integrate or checkpoint | next stage's preamble (handoff), orchestrator (`args.prior`, embedded by the launcher's `--prior`, §1) |
| `gates.json` | orchestrator at each gate, for demos too (spec-check needs the S1 hashes) | driver (`args.gates`), spec-check |
| `answers.md` | skeleton S1.synthesize; answers you or the client | S1.synthesize, S2.reference, S2.golden, S4.models, S5.features |
| `quote.md`, `estimate.json` | S1.synthesize (`estimate`, `estimate render`) | you, the client |
| `change-orders/CO-NNN.md` | request by the orchestrator; rest by S1.change_order | spec-check, gate. A missing `change-orders/` directory means no change orders. A change order counts as approved only through its gates.json `CO-NNN` entry. |
| `change-orders/CO-NNN.estimate.json` | S1.change_order (`estimate --delta <base_rules_sha256>`), rendered into the `.md` | you, the client |
| `stage1-cutover-report.md` | you, at the client S3 gate, in a plain terminal: after the load `mp-data stage1 <eng> --target-db secret:stage1_target_url_ref --dataset fresh-copy`, the check `mp-data stage1 <eng> --target-db secret:stage1_target_url_ref --dataset fresh-copy --verify --require-rows --report {{p.records}}/stage1-cutover-report.md` (counts only) | you, the client |
| `golive-report.md` | you, at the client S6 gate, Access kinds, in a plain terminal: after the go-live load `mp-data stage1 <eng> --target-db secret:production_db_url_ref --dataset fresh-copy`, the check `mp-data stage1 <eng> --target-db secret:production_db_url_ref --dataset fresh-copy --verify --require-rows --report {{p.records}}/golive-report.md` (counts only; the target runbook's Go-live and smoke checklist) | you, the client |
| `destruction-record-<YYYY-MM-DD>.md` | `mp-data purge` | you, the client |

`runs/<stage>.json`: the driver report minus `integrate` and `checkpoint` (those agents write the file). Rewritten on every attempt.
```json
{
  "engagement": "northwind-demo", "stage": "S2", "mode": "bootstrap", "attempt": 1,
  "branches": {"pipeline": "eng/northwind-demo/S2-characterize", "target": "northwind-demo/S2-characterize"},
  "tasks": [{"id": "S2.synth", "status": "done", "summary": "3 seeds, deterministic", "notes": [], "reused": false}],
  "results": {"S2.synth": {"status": "done", "files_changed": ["northwind/goldens/synthetic/seed-1/input/dataset.json"], "acceptance": [{"cmd": "<emitted command>", "exit_code": 0, "passed": true, "detail": ""}]}},
  "items": {},
  "issues": [], "blocking": [], "requests": [],
  "manual_steps": [{"task": "S2.reference", "who": "operator", "where": "windows-box", "step": "confirm 4 Format() queries in Access", "blocks_gate": false}],
  "pipeline_changes": [],
  "agents": {"estimate": {"typical": 7, "worst": 13}, "spawned": 7},
  "fix_rounds_used": 0, "output_tokens_turn": 0,
  "gate": {"name": "Behaviour recorded", "kind": "demo", "checklist": ["..."], "est_minutes": 40},
  "ready": true
}
```

`log.md` starts with exactly this header (S0.workspace):
```
| date | stage | attempt | run_id | mode | agents | est typical/worst | fix rounds | input tok | cache-write tok | cache-read tok | output tok | est cost USD | human gate min | human review min | human other min | ready | blockers |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
```
- Integrate or checkpoint appends one row: date from `date -u +%F`, stage, attempt, `pending` for run_id, mode, agents spawned + 1, `typical/worst`, fix rounds used, `see runbook` in the four token columns and the cost column, `pending` in the three human columns, `yes`/`no`, and the not-done task ids separated by `;` (or `none`). No cell contains `|`.
- The orchestrator fills run_id, tokens, cost and minutes (docs/runbook.md) and commits to the stage branch before merging.

`gates.json` (orchestrator):
```json
{
  "S0": {"approved": "2026-10-20"},
  "S1": {"approved": "2026-11-02", "signed_by": "Thomas Sodon (demo)", "rules_sha256": "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb", "rules_md_sha256": "3e23e8160039594a33894f6564e1b1348bbd7a0088d42c4acb73eeaed59c009d", "deposit_received": true},
  "CO-001": {"approved": "2026-11-20", "rules_sha256": "2e7d2c03a9507ae265ecf5b5356885a53393a2029d241394997265a1a25aefc6", "rules_md_sha256": "18ac3e7343f016890c510e93f935261169d9e3f565436429830faf0934f4f8e4"}
}
```
- Stage keys hold `approved` (date). S1 adds `signed_by`, `rules_sha256` and `rules_md_sha256` (sha256sum of `analysis/rules.json` and `analysis/rules.md` at the signed commit) and `deposit_received`.
- `CO-NNN` keys `{approved, rules_sha256, rules_md_sha256}` record approved change orders, written at the change-order gate; the highest-numbered approved one replaces the S1 hashes as the approved state for spec-check.
- `S3.requote` `{approved, usd}`: only when Stage 1 was sold alone (no S1 gate) and the S3 gate re-quoted it; the new price also goes into engagement `commercial.quote`. The S3 entry keeps it when `approved` is added.
- Demos write it too; the entry is committed on the pipeline stage branch before the squash-merge.

`answers.md` (one heading per open question; the heading line is exactly `## Q-<area>-NNN`; rules, never data):
```
## Q-orders-001
question: Are discounts applied before tax?
Discounts apply to the net amount before VAT.
answered: 2026-11-01
by: client
```
The S1.synthesize skeleton has the heading, the `question:` line and `answered: open`. An answer adds the text, the date and `by: client` or `by: you`.

`change-orders/CO-NNN.md`:
```
# CO-001: add the backorder report
requested: 2026-11-18
base_rules_sha256: ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb
rules_sha256_after: 2e7d2c03a9507ae265ecf5b5356885a53393a2029d241394997265a1a25aefc6
rules_md_sha256_after: 18ac3e7343f016890c510e93f935261169d9e3f565436429830faf0934f4f8e4
approved:

## Request
## Rules added
## Rules changed
## Objects changed
<!-- estimate:begin -->
<!-- estimate:end -->
```
Header lines start at column 0. The orchestrator writes the title, `requested` and `## Request` before the run and passes `args.change_order`; S1.change_order writes the rest; `approved:` stays empty until the gate.

Change orders need an approved S1 gate. Without one (Stage 1 sold alone) the S3 gate re-quotes instead: gates.json `S3.requote {approved, usd}` and a new `commercial.quote`.

`estimate.json` (`estimate <inventory.json> [--rules <rules.json>] [--delta <rules_json_sha256>] --out <file.json>`), rendered into `quote.md` between `<!-- estimate:begin -->` and `<!-- estimate:end -->` by `estimate render <estimate.json> --into <quote.md>`, which changes nothing else:
```json
{"format_version": 1, "inventory_sha256": "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb", "rules_sha256": "3e23e8160039594a33894f6564e1b1348bbd7a0088d42c4acb73eeaed59c009d", "coefficients_sha256": "18ac3e7343f016890c510e93f935261169d9e3f565436429830faf0934f4f8e4", "hours": {"low": 128, "point": 160, "high": 200}, "tier": "stage2-standard", "rate_usd_per_hour": 130, "price_usd": {"low": 16700, "point": 20800, "high": 26000}, "price_note": null, "stage1": {"hours": {"low": 16, "point": 20, "high": 25}, "price_usd": {"low": 2100, "point": 2600, "high": 3300}}, "by_area": {"orders": 40, "purchasing": 25}, "delta": null}
```
- `hours` `{low, point, high}`: `point` from the coefficients in `estimate.toml` (S0.healthcheck), `low` = 0.8 × point, `high` = 1.25 × point. Gate texts quote the point hours.
- `tier`: the Stage 2 row of `pipeline.json` `pricing_tiers` whose hours range holds the point estimate (`stage2-small` up to 90 h, including estimates under its 50 h floor; `stage2-standard` from 110 h); between 90 and 110 h `between stage2-small and stage2-standard: decide at S1`; above 200 h `above stage2-standard: split into phases`; with more than one data back end `custom quote: more than one data back end`. quote.md takes `price_usd.point`, states any clamp, and subtracts `commercial.assessment_credit_usd` as a credit line.
- `rate_usd_per_hour`: engagement `commercial.rate_usd_per_hour` when set (estimate reads it from inventory.json `rate_usd_per_hour`, §5), else `pipeline.json` `pricing.rate_usd_per_hour`.
- `price_usd` `{low, point, high}`: the `pipeline.json` `pricing` rule, `ceil(hours × rate / round_to_usd) × round_to_usd` for each of low, point and high. `point` is clamped to the `price_usd` range of the `tier` row, and `price_note` names any clamp (null when there is none). When `tier` is not a row of `pricing_tiers` (between rows, above stage2-standard, custom quote), nothing is clamped and `price_note` says why.
- `stage1` (Access kinds; null for xlsm): `{hours {low, point, high}, price_usd {low, point, high}}` from the `[stage1]` table of `estimate.toml`, with low and high as for `hours`, priced by the same rule and clamped to the `stage1` tier row (`price_note` names that clamp too).
- `rules_sha256` and `by_area` (hours from the kept objects of each area) only with `--rules`, else null.
- `--out` is byte-deterministic (sorted keys, no timestamps), so a re-run into a temp dir compares with `cmp`.
- With `--delta <sha>`, estimate finds the committed version of the `--rules` file whose sha256 matches (`git log` and `git show` in its repo) and prices only the rules added or changed since: `delta` is `{"base_rules_sha256": "<sha>", "by_area": {<hours difference>}, "price_usd": <the summed by_area hours priced by the pricing rule, unclamped>, "added": [...], "removed": [...], "changed": [...]}` (rule ids). Exit 2 when no committed version matches.

Destruction record (`mp-data purge`, `{{p.records}}/destruction-record-<YYYY-MM-DD>.md`, no file contents and no values):
```
# Destruction record: c2026001
date: 2027-03-01
engagement: c2026001

## Deleted
| path | files | bytes |
|---|---|---|
| private/sources | 2 | 14680064 |

## Databases dropped
| server | name |
|---|---|
| postgresql | mpdata_c2026001_stage1 |
| postgresql | c2026001_dev |

## Servers not checked
| server | reason |
|---|---|
| mssql | container not answering |

## Sources
| path | sha256 |
|---|---|
| sources/data/c2026001_be.accdb | 2e7d2c03a9507ae265ecf5b5356885a53393a2029d241394997265a1a25aefc6 |

## Copies to delete by hand
| path | what |
|---|---|
| C:\mp\c2026001\frontend-relinked.accdb | the relinked front-end copy from the S3 rehearsal |
```
- `Deleted`: one row per top-level directory of `{{p.private}}`. `Databases dropped`: every database purge dropped (§1 Databases). `Servers not checked`: each local dialect without a server URL or whose server did not answer, with the reason, or the line `none`. `Sources`: from source.lock.json. `Copies to delete by hand`: every entry of engagement `environment.windows_box.copies` (`{path, what}`), or the line `none`; you delete them and note the date beside the record.

Failure:
- `estimate render` exits 2 when the markers are missing. `estimate render --check` exits 1 when the markers are missing or the block would change.
- `spec-check` hashes the raw bytes of `analysis/rules.json` and `analysis/rules.md`. Exit 0 when they equal the approved state: gates.json S1, replaced by the highest-numbered gates.json `CO-NNN` entry that has `approved` set. Also exit 0, printing `pending CO-NNN: approve at the gate`, when they equal the `_after` hashes of the highest-numbered `change-orders/CO-NNN.md` that has no approved gates.json entry and whose `base_rules_sha256` is the approved rules hash. A missing `change-orders/` directory means no change orders, not an error. Exit 1 otherwise (`spec not signed` when gates.json has no S1 hashes); exit 2 only when `rules.json`, `rules.md` or gates.json is missing.

---

<a id="documents"></a>
## 16. documents

- **Units:** `invoice_pdf`, `invoice_xlsx`, `email_draft` (xlsm engagements), keyed by invoice number.
- **Producer:** S2.reference_xlsm (fields from the shipped outputs for the engagement dataset; seed documents emulated from the macro spec and proved on the shipped invoices by `reference selfcheck`). The emulation spec (cell map, file-name pattern, rule ids) is `{{p.target}}/goldens/reference/functions/documents.json`; text the macro builds is read at run time from the model, never committed.
- **Consumers:** golden record (S2.golden), the harness runner, `DjangoAdapter.render_document` (S4.data), the documents area (S5).

```json
{
  "15001": {
    "invoice_number": "15001", "invoice_date": "2024-03-01T00:00:00", "due_date": "2024-03-21T00:00:00",
    "customer": "Example Co", "company": "Example Trading Ltd",
    "lines": [{"service": "Design work", "date": "2024-02-20T00:00:00", "amount": "100.0000"}],
    "net": "100.0000", "vat": "18.0000", "total": "118.0000"
  }
}
```
Canonical fields:
- `invoice_pdf`, `invoice_xlsx`: `invoice_number`, `invoice_date`, `due_date`, `customer`, `company`, `lines[{service, date, amount}]` (non-empty lines in line order), `net`, `vat`, `total`.
- `email_draft`: `to`, `subject`, `body_text`, `attachment_names` (sorted).
- Values are canonical strings (§10): dates as datetime, amounts as currency. Text fields are whitespace-normalised (runs of whitespace become one space, trimmed), because PDF text extraction does not keep spacing.

Readers:
- **PDF:** pypdf text extraction, mapped to the fields by the Template layout.
- **xlsx:** the shipped samples are Strict OOXML, which openpyxl 3.1.5 cannot load (`TypeError: ExternalReference.__init__() missing 1 required positional argument: 'id'`). Rewrite these URIs in every `.xml` and `.rels` part of a temp copy and drop ` conformance="strict"`, then read the Template cell map (tested: all 3 samples load after the rewrite):

| Strict (`http://purl.oclc.org/ooxml/...`) | Transitional |
|---|---|
| `officeDocument/relationships/extendedProperties` | `http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties` |
| `officeDocument/relationships` (also as a prefix of relationship types) | `http://schemas.openxmlformats.org/officeDocument/2006/relationships` |
| `officeDocument/extendedProperties` | `http://schemas.openxmlformats.org/officeDocument/2006/extended-properties` |
| `officeDocument/docPropsVTypes` | `http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes` |
| `spreadsheetml/main` | `http://schemas.openxmlformats.org/spreadsheetml/2006/main` |
| `drawingml/main` | `http://schemas.openxmlformats.org/drawingml/2006/main` |
| `drawingml/spreadsheetDrawing` | `http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing` |

Apply the first row before the second.
- **.msg** (Outlook OLE files), read with olefile (BSD), never extract-msg (GPL):

| MAPI property | Stream | Field |
|---|---|---|
| 0x0037 subject | `__substg1.0_0037001F` (UTF-16LE; `001E` = 8-bit) | `subject` |
| 0x0E04 display-to | `__substg1.0_0E04001F` | `to` |
| 0x1000 body | `__substg1.0_1000001F` | `body_text` |
| 0x3707 attachment long name | `__attach_version1.0_#0000000N/__substg1.0_3707001F` | `attachment_names` |

- The source ships 3 PDFs, 3 xlsx and 2 .msg files (no .eml). One invoice has no .msg: its `email_draft` key is absent and reported as a gap, not a failure.
- The Django app produces an `.eml` with the PDF attached; its fields are read with the stdlib `email` package into the same canonical fields.
- Document goldens are JSON, never PDF or xlsx (the `forbid-sample-outputs` hook rejects those). The Excel invoice is built in code with openpyxl, never from a committed .xlsx template.

Failure: a field that cannot be extracted makes the unit `unsupported` with reason `other` at record time; at compare time a field difference fails the document test, and the report names the field, never its value.

---

<a id="summary-schema"></a>
## 17. summary-schema

<a id="summary-json"></a>
### summary.schema.json (mp-data stdout)
- **Path:** `pipeline:toolkit/schemas/summary.schema.json` (S0.workspace).
- **Producer:** every toolkit command under `MP_QUIET=1` (§3); mp-data adds `log`, and writes the `not-built`, `error` and `pin` summaries itself.
- **Consumers:** mp-data (validation), agents, reviewers, the orchestrator.

```json
{
  "status": "failed",
  "command": "equivalence",
  "engagement": "northwind-demo",
  "exit_code": 1,
  "counts": {"cells": 8, "kept_units": 63, "passed": 470, "tests": 495},
  "rates": {"min_cell": 0.8571, "pooled": 0.9495, "coverage_adjusted": 0.9325},
  "cells": [{"dataset": "engagement", "db": "mssql", "status": "ok", "passed": 54, "failed": 8, "errors": 0, "skipped": 0, "not_implemented": 1, "total": 63, "rate": 0.8571}],
  "units": [{"id": "qryOrderTotals", "status": "failed", "reason": null, "dataset": "engagement", "db": "mssql"}],
  "sha256s": {"manifest.json": "cd0aa9856147b6c5b4ff2b7dfee5da20aa38253099ef1b4a64aced233c9afe29"},
  "columns": {"qryOrderTotals": ["OrderID", "Total"]},
  "mismatches": [{"unit": "qryOrderTotals", "dataset": "engagement", "db": "mssql", "columns": {"Total": 3}}],
  "paths": ["$HOME/sourcecode/access-excel-to-django/northwind/goldens/manifest.json"],
  "messages": ["cell engagement/mssql below 0.9"],
  "log": "$HOME/engagements/northwind-demo/private/logs/20261016T120000Z-s5_review_r1-48211-equivalence.log"
}
```
A stage1 summary carries `tables`:
```json
{"status":"ok","command":"stage1","engagement":"northwind-demo","exit_code":0,"counts":{"dialects":2,"tables":28,"rows":3155},"tables":[{"table":"Orders","dialect":"postgresql","dataset_rows":48,"target_rows":48,"digest_match":true,"fk_orphans":0}],"messages":[],"log":"$HOME/engagements/northwind-demo/private/logs/20261016T110000Z-s3_move-50112-stage1.log"}
```

| Field | Rule |
|---|---|
| `status` | required: `ok`, `recorded`, `no-data`, `not-built` (all exit 0), `failed` (exit 1 or 5) or `error` (anything else) (§3) |
| `command` | required: the subcommand words, for example `golden record` |
| `exit_code` | required integer |
| `engagement` | the engagement id, when the command takes `<eng>` |
| `counts` | object of non-negative integers |
| `rates` | object of numbers from 0 to 1 (`min_cell`, `pooled`, `coverage_adjusted`, `characterization`, `empty_share`, `match`) |
| `cells` | equivalence: `{dataset, db, status ok\|empty, passed, failed, errors, skipped, not_implemented, total, rate}` (`rate` null when empty). results: the results.json cell fields without `not_passed` (`suite`, no `status`, `rate` 0 when `total` is 0; §12) |
| `units` | `{id, status, reason}` with optional `dataset`, `db`, `matched`, `total`: unit ids with statuses and reasons, or per-unit match counts (`reference selfcheck`); in `reference run` summaries `total` is the unit's row count (status `empty` for 0 rows, §9); never values |
| `tables` | `{table, dialect, dataset_rows, target_rows, digest_match, fk_orphans}` per dialect and table (stage1); a field a command cannot fill is null (dataset export: `dialect`, `target_rows`, `digest_match`, `fk_orphans`) |
| `sha256s` | object of 64-hex digests (never keys) |
| `columns` | table or unit name to column names |
| `mismatches` | `{unit, dataset, db, columns: {name: count}}`, counts only |
| `paths` | absolute paths inside `{{p.pipeline}}`, `{{p.target_repo}}`, `{{p.records}}` or the design zone of `{{p.private}}` (`out/`, `review/`, `synthetic/`) |
| `messages` | at most 50 strings of at most 200 characters, no data values |
| `log` | set by mp-data: the log file path; absent for `not-built` and `pin` |

No other keys at any level (`additionalProperties` false). The whole summary is one line.

`{{p.private}}/review/<slot>/` is reviewer scratch space in the design zone: it holds only quiet summaries, regenerated design files and synthetic outputs. Reviewers may read their outputs there, never a data-zone path.

<a id="client-session-settings"></a>
### Client-session settings
Client sessions (`bootstrap` false) run without the Claude Code sandbox: on Linux a sandboxed command's `localhost` is private to it, so it cannot reach the Docker databases on 127.0.0.1, and `docker` does not work inside the sandbox. Isolation comes from three parts instead: generated settings with a Bash hook (this section), the mp-run wrapper (below) and the database roles (§1 Databases).

- **Path:** `$HOME/engagements/<id>/client-session.json`, chmod 600. Generated by `pipeline:scripts/client-settings.sh <id>` (S0.workspace; bash, jq and git; `<id>` is its only argument; prints the JSON on stdout; the runbook writes and chmods the file). It needs no other file from the pipeline checkout, so it also works piped: `git show origin/main:scripts/client-settings.sh | bash -s -- <id>`. The deny list names the engagement directories present when it runs, so the runbook regenerates it from main before every session start. An `<id>` that does not match the engagement id pattern (§1), or a missing `$HOME/engagements/<id>`, exits 2.
- **Used by:** `claude --settings $HOME/engagements/<id>/client-session.json ...` (§1 Session start). `--settings` is the command-line scope: it overrides project and user values key by key, and list keys merge across scopes. Never rely on `~/.claude/settings.json` alone.

Top-level keys are exactly `disableAllHooks`, `hooks`, `permissions` and `sandbox`. Example for engagement `c2026001` on a host where `$HOME` is `/home/op` and one other engagement, `c2026002`, exists (the deny list is shortened; the table below gives all of it):
```json
{
  "disableAllHooks": false,
  "hooks": {
    "PreToolUse": [
      {"matcher": "Bash", "hooks": [{"type": "command", "command": "/home/op/.local/bin/mp-bash-hook"}]}
    ]
  },
  "permissions": {
    "disableBypassPermissionsMode": "disable",
    "allow": ["Bash(mp-run:*)", "Bash(mp-data:*)"],
    "deny": [
      "Read(//home/op/engagements/c2026001/private/sources/**)",
      "Edit(//home/op/engagements/c2026001/private/sources/**)",
      "Write(//home/op/engagements/c2026001/private/sources/**)",
      "Read(//home/op/engagements/c2026001/private/scrub-map.json)",
      "Edit(//home/op/engagements/c2026001/private/scrub-map.json)",
      "Write(//home/op/engagements/c2026001/private/scrub-map.json)",
      "Read(//home/op/engagements/c2026002/private/**)",
      "Read(//home/op/engagements/c2026002/record/**)",
      "Read(//home/op/.config/modernization/data.env)",
      "Edit(//home/op/.config/modernization/**)",
      "Write(//home/op/.config/modernization/**)",
      "Edit(//home/op/engagements/c2026001/client-session.json)",
      "Write(//home/op/engagements/c2026001/client-session.json)",
      "Edit(//home/op/.claude/**)",
      "Write(//home/op/.claude/**)",
      "Edit(//home/op/sourcecode/modernization-pipeline/.git/hooks/**)",
      "Write(//home/op/sourcecode/modernization-pipeline/.git/hooks/**)",
      "Read(//proc/**)",
      "Monitor"
    ]
  },
  "sandbox": {"enabled": false}
}
```

| Deny rules | Paths |
|---|---|
| `Read`, `Edit`, `Write` | every data-zone path of this engagement (§1): `private/sources/**`, `datasets/**`, `workbook/**`, `reference/**`, `goldens/**`, `corpus/**`, `results/**`, `logs/**`, `assets/**`, and the file `private/scrub-map.json` (no `/**`) |
| `Read`, `Edit`, `Write` | every other engagement's `private/**` and `record/**` under `$HOME/engagements/` |
| `Read`, `Edit`, `Write` | `$HOME/.config/modernization/data.env` |
| `Edit`, `Write` | `$HOME/.local/bin/**`, `$HOME/.local/share/modernization/**`, `$HOME/.config/modernization/**`, `$HOME/.claude/**`, `$HOME/.claude.json`, and the settings file itself |
| `Edit`, `Write` | when `record/paths.local.json` exists: the pipeline worktree's `.claude/**` and `.mcp.json`, and `hooks/**` and `config` in the git common directory (`git rev-parse --path-format=absolute --git-common-dir`) of the pipeline and target repos. These are the files mp-run binds read-only (§17 mp-run): the Edit and Write tools act outside mp-run, and these files configure code that runs outside any jail. |
| `Read` | `/proc/**` (written `Read(//proc/**)`): the Read tool runs outside any jail, `/proc/<pid>/environ` of a data-plane child holds its `MP_SECRET_` values (§2), and `/proc/<pid>/root` reaches every denied path under another name |
| tool | `Monitor` |

- Rule paths are absolute: `//` followed by the expanded path without its leading `/`. The script expands every path; no path segment contains `*`, `?` or `[` (only the trailing `/**`).
- `disableAllHooks` false: the command-line scope wins key by key, so no project or user settings file can switch the hook off.
- `disableBypassPermissionsMode` `"disable"` stops a session from skipping permission checks.
- `permissions.allow` is exactly `["Bash(mp-run:*)", "Bash(mp-data:*)"]`, so workflow agents' many distinct mp-run and mp-data calls do not each wait for approval. The PreToolUse Bash hook still runs first and refuses every other shape (§17 mp-bash-hook); the deny rules still apply.
- Nothing in the design zone (`out/`, `review/`, `synthetic/`) or in this engagement's `record/` is denied.
- Not used: `sandbox.excludedCommands`, `network.allowedDomains`, environment-variable prefixes on data-plane calls.
- S0.workspace's acceptance checks the script's output for a fixture engagement with jq: the exact top-level keys, `disableAllHooks` false, `sandbox.enabled` false, the hook entry, `permissions.allow` exactly `["Bash(mp-run:*)", "Bash(mp-data:*)"]`, `Read`, `Edit` and `Write` denies for every data-zone path of §1, the other engagement and data.env, the `Edit` and `Write` denies, `Read(//proc/**)`, `Monitor`, nothing denied in the design zone or this engagement's `record/`, and no `*`, `?` or `[` inside a path segment.
- The driver tells client agents: "This session blocks any Bash call that is not one mp-run or mp-data call. Run ad-hoc commands as `mp-run --eng <eng> --slot <your slot> -- '<command>'`." The Read, Grep and Glob tools work except on denied paths.
- For client engagements the driver also appends the S0 exit criterion `test "$(cat {{p.private}}/sources/* 2>/dev/null | wc -c)" = 0`; it runs through mp-run and proves the data zone reads empty there.
- Before the first client S0 (and on every new host) the runbook's probe (docs/runbook.md 1.13) checks, in seven steps: (1) in the session, a plain `ls` Bash call is blocked; (2) `mp-run --eng ... --slot probe -- 'cat <private>/sources/* | wc -c'` prints 0; (3) the same `ls` from a one-agent workflow is blocked too; (4) `mp-data --slot probe fetch <eng> --verify --record-if-unset` prints status `ok` or `recorded`; (5) in a plain terminal, the `postgres` environment check passes; (6) in a plain terminal, `toolkit/tests/test_mp_run.py` from the pinned release (`uv run --no-sync` with `UV_CACHE_DIR=$HOME/.local/share/modernization/uv-cache`) passes with no skips; (7) a symlink from `<private>/review/` to a file in `<private>/logs/` is refused by the Read and Grep tools. Any failure stops the engagement before S0.
- The environment preflight and every gate step that touches Docker or data.env run in a plain terminal, outside Claude.

<a id="mp-bash-hook"></a>
### mp-bash-hook
- **Path:** `pipeline:scripts/mp-bash-hook` (S0.workspace), run with `python3 -I`, standard library only; installed as `$HOME/.local/bin/mp-bash-hook` by `install-mp-data.sh`. Its unit tests cover the allowed and blocked forms below.
- **Input:** the PreToolUse hook JSON on stdin; it reads `tool_input.command`.
- **Allows** (exit 0, no output) only one simple command whose first word is exactly `mp-run` or `mp-data` (not a path to them):
  - `mp-run` exactly as `mp-run --eng <path> --slot <slot> -- <one command string>`;
  - `mp-data` with any arguments, except that `pin` is allowed only as `mp-data pin --status`.
- **Checks**, in order: the raw text has no newline or carriage return, no `$` or backtick outside single quotes (bash would expand them before mp-run or mp-data starts) and no unbalanced quote; shlex drops quoting, so these are scanned first. Then the command is split with `shlex.shlex(command, posix=True, punctuation_chars=True)` and `whitespace_split`; no token may consist only of the characters `( ) ; < > | &` (operators, redirections such as `2>&1`, subshells, background), and there is no leading `NAME=value` assignment.
- **Blocks** anything else, and input it cannot parse, with exit 2 and this text on stderr: `client session: run shell commands as mp-run --eng <eng> --slot <slot> -- '<command>', or a data-plane call mp-data ...`.
- Hooks in `--settings` apply to workflow agents too; the first-session probe checks it.

<a id="mp-run"></a>
### mp-run
- **Path:** `pipeline:scripts/mp-run` (S0.workspace, bash); installed as `$HOME/.local/bin/mp-run` by `install-mp-data.sh`. The `mp_data` environment item checks that `mp-data` and `mp-run` resolve to those copies and that all three scripts are regular files with mode 755. The `bwrap` environment item (all stages, all engagements; mp-data's jail needs it too) checks `bwrap --unshare-user --unshare-pid --die-with-parent --dev-bind / / --proc /proc true`.
- **Form:** `mp-run --eng <records>/engagement.json --slot <slot> -- '<command>'`, exactly one quoted command string after `--` (anything else exits 2).
- **What it does:**
  1. Reads `<records>/paths.local.json`; sources `$HOME/.config/modernization/env` (never data.env); exports `MP_DB_SUFFIX=<slot>` and `MP_SANDBOX=mp-run`; unsets `DOCKER_HOST`.
  2. Changes to the pipeline path unless the command starts with `cd `.
  3. Runs `bash -c '<command>'` under `bwrap --unshare-user --unshare-pid --die-with-parent --dev-bind / / --proc /proc` plus, in this order:
     - first every read-only bind (`--ro-bind`, each when present): `$HOME/.local/bin`, `$HOME/.local/share/modernization` (reviewed releases, their `.venv` and `uv-cache`), `$HOME/.local/share/uv` (uv's Python installs), `$HOME/.config/modernization`, `$HOME/.claude`, `$HOME/.claude.json`, `$HOME/engagements/<id>/client-session.json`, the pipeline worktree's `.claude/` and `.mcp.json`, and the `hooks/` directory and `config` file of the git common directory (`git -C <repo> rev-parse --path-format=absolute --git-common-dir`) of the pipeline repo and the target repo;
     - then the masks: `--tmpfs` over every data-zone directory of this engagement (§1; created first when missing), so they read empty and writes there are discarded; `--tmpfs` over every other engagement's `private/` and `record/` under `$HOME/engagements/`, and over the `pass` store directory (`${PASSWORD_STORE_DIR:-$HOME/.password-store}`) when present (`--ro-bind /dev/null` cannot cover a directory: bwrap 0.11.1 stops with `Is a directory`); `--tmpfs` over `$XDG_RUNTIME_DIR` (default `/run/user/<uid>`), `${TMUX_TMPDIR:-/tmp}/tmux-<uid>` and `/run/screen/S-<user>` (each when present), as in mp-data's jail (§3): the session bus there reaches the systemd user manager, which starts commands outside mp-run, and rootless Docker and containerd keep their sockets there; `--ro-bind /dev/null` over `{{p.private}}/scrub-map.json`, `$HOME/.config/modernization/data.env`, `/var/run/docker.sock` and the unix socket that `DOCKER_HOST` named when it lies outside `$XDG_RUNTIME_DIR` (each when present), so they cannot be read.
  4. The order matters: bwrap applies its mount options in the order given, so a later mount wins, and a directory bind placed after a mask uncovers the masked file. Tested with bubblewrap 0.11.1: with `--ro-bind /dev/null <dir>/data.env` before `--ro-bind <dir> <dir>`, the file is readable again; with the directory bind first, reading it fails with `Permission denied`. So all read-only binds come first and the masks last.
  5. Keeps the host network, so `127.0.0.1:55432` and `127.0.0.1:14330` stay reachable. Docker is not.
  6. Exit code = the command's.
- **Why those binds:** processes outside mp-run read them: Claude Code reads its settings, hooks and MCP configuration (`$HOME/.claude`, `$HOME/.claude.json`, the worktree's `.claude/` and `.mcp.json`), your plain-terminal git runs the repos' hooks and config (`core.fsmonitor`, filters), and mp-data runs the reviewed release and its `.venv`. Read-only binds stop agent commands from changing any of them. A bound git `config` cannot be rewritten, so `git push -u` inside mp-run prints `could not write config file` and still exits 0, and `git config` or `git remote` writes fail; the `hooks` environment item installs git hooks outside mp-run. Pre-commit and uv caches under `$HOME/.cache` stay writable, so your plain-terminal git on client repos runs with `-c core.hooksPath=/dev/null` (docs/runbook.md). The rest of `$HOME` (dotfiles, git and gh config, the records repo's git hooks and config) stays writable as well: a recorded residual risk, not fixed (§1 Threat model).
- **Credentials inside mp-run:** credential stores reached through `$XDG_RUNTIME_DIR` (a D-Bus keyring, ssh-agent, gpg-agent) do not work there, so git and gh inside mp-run use gh's file-stored token (`gh auth login --insecure-storage`; `gh auth status` names `hosts.yml`, which the `gh` environment item checks).
- **Used by:** the driver for every non-data-plane command from S1 on, and in S0 delivery runs (§1 Command forms). From S1 on, integrate and checkpoint agents run git and `date` through it with their own slot (your own git and gh credentials, through gh's file-stored token), demos as well as clients; an S0 bootstrap run uses the braced form instead. Demos use mp-run too, so a task that only works by reading the data zone fails in the demo already.
- **Tests** (S0.workspace, `toolkit/tests/test_mp_run.py`): a fixture engagement in a temporary HOME proves that a data-zone file reads empty, another engagement's `private/` is empty, data.env is unreadable although `$HOME/.config/modernization` is bound read-only before its mask (the order case), writes to `$HOME/.local/bin`, the pipeline's `.claude/` and its git hooks directory fail, `docker info` fails, a unix socket listener the test starts in a temporary `XDG_RUNTIME_DIR` is unreachable, a 127.0.0.1 listener is reachable, `$MP_DB_SUFFIX` is set, the working directory is the pipeline and exit codes (0, 1 and 7) pass through. The test skips when `MP_SANDBOX` is `mp-run`: Ubuntu's AppArmor profile for bwrap refuses a user namespace inside bwrap, so it runs only where mp-run is not around it (S0 bootstrap acceptance, pipeline CI, the first-session probe).

---

<a id="cli-registry"></a>
## 18. cli-registry

`toolkit <subcommand>` (cli.py discovers one `toolkit/commands/<name>.py` per subcommand). "Data plane" yes means the command runs as a data-plane call (`mp-data`, §3) whenever it touches the engagement data; agents run the others with `uv run toolkit`. Exit codes follow the Conventions table unless noted.

| Subcommand | Form and flags | Owner | Data plane |
|---|---|---|---|
| `fetch` | `fetch <eng> --verify [--record-if-unset]` (also restores the vendor jars, every source kind); `fetch --jars` | S0.workspace | yes (`--jars`: no) |
| `engagement-lint` | `engagement-lint <eng>` | S0.workspace | no |
| `parse` | `parse <eng> --strict --out {{p.private}}/out/model [--fail-on compiled-only,missing-backend,multi-backend,unresolved-link]` | S0.parse | yes |
| `scrub` | `scrub --check {{p.private}}/out/model` | S0.parse | no |
| `dataset` | `dataset export <eng> --out {{p.private}}/datasets/engagement [--check]`; `dataset validate <dir> --schema <schema.json>` | S0.dataset | export: yes; validate: no for seeds |
| `inventory` | `inventory <eng> --model {{p.private}}/out/model --out {{p.private}}/out/inventory.json --expect` (exit 0 with status `recorded` when it records the lock) | S0.healthcheck | no |
| `healthcheck` | `healthcheck {{p.private}}/out/inventory.json --out {{p.target}}/docs/health-check.md [--check]` (flags more than one data back end as `custom quote`, and a back end not received, §2) | S0.healthcheck | no |
| `estimate` | `estimate {{p.private}}/out/inventory.json [--rules <rules.json>] [--delta <rules_json_sha256>] --out <file.json>`; `estimate render <estimate.json> --into <quote.md> [--check]` | S0.healthcheck | no |
| `guard` | `guard <eng> --repo <path> [--repo <path>] [--staged \| --history] [--report DIR]`; `guard --hooks-only --repo <path> [--repo <path>]` (CI: no engagement, no corpus) | S0.guard | yes (`--hooks-only`: no) |
| `push` | `push <eng> --branch <branch>` (§3) | S0.workspace | yes, always |
| `gh` | `gh <eng> -- pr <create\|view\|list\|checks\|merge> ...` or `gh <eng> -- run <list\|view> ...` (§3) | S0.workspace | yes, always |
| `rules-lint` | `rules-lint <rules.json>` | S1.tooling | no |
| `rules-coverage` | `rules-coverage <rules.json> {{p.private}}/out/inventory.json --require 100` | S1.tooling | no |
| `rules-render` | `rules-render <rules.json> --keep-drop <md> [--check]` | S1.tooling | no |
| `spec-check` | `spec-check {{p.target}}/analysis --gates {{p.records}}/gates.json --change-orders {{p.records}}/change-orders/` | S1.tooling | no |
| `schema-lint` | `schema-lint <data-model.json>` | S1.tooling | no |
| `reference` | `reference sql <eng> --all`; `reference build <eng> --dataset engagement\|seeds\|seed-N` (`all` exits 2 naming the two commands, §9); `reference run <eng> --dataset <name> --all --summary`; `reference selfcheck <eng> --min-match 1.0` (xlsm) | S2.reference; S2.reference_xlsm co-owns `commands/reference.py` | `sql`, `build --dataset seeds\|seed-N`, `run` on a seed: no; `build --dataset engagement`, `run` on engagement, `selfcheck`: yes |
| `synth` | `synth <eng> --schema-from auto --seeds 1,2,3 --out {{p.target}}/goldens/synthetic [--check]` | S2.synth | no |
| `golden` | `golden record <eng> [--dataset engagement\|seeds\|all] [--private-only] [--check]`; `golden status {{p.target}}/goldens --rules {{p.target}}/analysis/rules.json --dataset engagement --min-ok 0.9 --max-empty 0.25` | S2.golden | `record` with engagement or all: yes; `record --dataset seeds`, `status`: no |
| `equivalence` | `equivalence <eng> --adapter reference\|django --datasets synthetic\|engagement\|all\|<list> [--dbs postgresql\|mssql\|from-engagement] [-k EXPR] [--only-areas A[,B]] [--collect-only] [--allow-not-implemented] [--allow-empty] [--min-pass-per-cell F] [--junit-dir DIR] [--matrix FILE]` (§12; plugin options `--eq-adapter`, `--eq-dataset`, `--eq-db`, `--eq-allow-not-implemented`, `--eq-only-areas`) | S2.harness | yes when the datasets include `engagement` |
| `vendor-harness` | `vendor-harness --target {{p.target}} [--check]` | S2.harness | no |
| `relink-scan` | `relink-scan <eng> --model {{p.private}}/out/model --dialects from-engagement --out {{p.target}}/docs/stage1-frontend-changes.md --fail-on unsupported-type [--check]` | S3.relink_scan (also `toolkit/typemap.py`, the neutral-type mapping stage1 and S4.models use) | no |
| `stage1` | `stage1 <eng> --target-db local:<name>\|secret:<ref>\|URL [--dataset engagement\|DIR\|fresh-copy] [--schema-from auto\|model\|data-model] [--schema-only] [--replace] [--verify] [--require-rows] [--dialects from-engagement] [--report FILE]` | S3.move; S4.models adds `--schema-from data-model` | yes with engagement, fresh-copy or a `DIR` under `{{p.private}}`; no with `--schema-only` or a synthetic `DIR` |
| `new-app` | `new-app <eng> --out {{p.target}}/app [--only core] [--check]` (`--only core` applies the overlay's `core/` and never touches `core/local/`, §14) | S4.template | no |
| `results` | `results summarize <eng> --junit-dir DIR [--junit-dir DIR] --out FILE` (§12) | S6.report | yes |
| `report` | `report <eng> --results {{p.target}}/equivalence/results.json --out {{p.target}}/docs/equivalence-report.md [--check]` | S6.report | no |
| `purge` | `purge <eng> [--dry-run]` | S6.report | yes |
| `wireframe` | `wireframe <eng> --model {{p.private}}/out/model --workflows {{p.target}}/analysis/rules.json --out {{p.target}}/docs/img/before [--check]` | S6.visuals | no |
| `wireframe-xlsm` | `wireframe-xlsm <eng> --dataset {{p.target}}/goldens/synthetic/seed-1/input --out {{p.target}}/docs/img/before [--check]` | S6.visuals_xlsm | no |

Notes:
- `fetch --verify`: for every source kind it also restores missing or wrong jars of `stack.reference_engine.jars` into the toolkit root's `vendor/` (§6), a declared output of fetch in the jail (§3).
- `estimate --delta <sha>`: see §15. `estimate render --check` exits 1 when the markers are missing or the block would change; without `--check`, missing markers exit 2.
- `guard` checks the candidate set `git ls-files -z --cached --others --exclude-standard` of each repo; `--staged` checks `git diff --cached --name-only`; `--history` checks every blob reachable from any ref and lists refs not merged into main. It rebuilds its corpus from the source on each run (exit 2 when the source is missing) and reads its parameters from `stack.guard`. Its file checks are the repos' own hooks `forbid-source-files`, `forbid-sample-outputs` and `check-added-large-files`; guard exits 2, running no hook, when a repo's `.pre-commit-config.yaml` defines them differently from S0's configs (local `language: fail` forbid hooks; pre-commit-hooks v6.0.0 check-added-large-files with `--enforce-all --maxkb=1024` in the pipeline repo, whose pipeline.json is over 500 KB (about 540 KB on disk), and `--enforce-all --maxkb=500` in the target repo), because it must never run a command a repo defines. `--history` applies each repo's own limit to its blob sizes. Under mp-data, pre-commit uses `PRE_COMMIT_HOME=$HOME/.local/share/modernization/pre-commit`. `--hooks-only` runs only those hooks and needs no engagement. For demos (`bootstrap` true, `target.mode` github) both integrate and the checkpoint agent run `mp-data --slot <slot> guard <eng> --repo <pipeline> --repo <target_repo> --history` before they push (`s<N>_integrate`, `s<N>_checkpoint`), next to `guard --staged`; the demo gate and every push after the public flip run it too. In an S0 bootstrap run before `toolkit/commands/guard.py` exists, the checkpoint falls back to a regex check of the staged names against the forbidden-file pattern.
- `stage1 --target-db`:
  - `local:<name>`: database `<name>` on the local server of each dialect that `--dialects` selects (`from-engagement` = `database.primary` plus `database.verify_also`; required with `local:`, so stage1 runs once per dialect). The server URL is `MP_DATA_PG_URL` or `MP_DATA_MSSQL_URL` when set, else `PG_URL` or `MSSQL_URL`; a dialect with neither exits 2 naming the variable. Inside mp-data, `PG_URL` and `MSSQL_URL` hold the data.env superuser URLs, because mp-data maps `MP_DATA_*` onto them for stage1 (a toolkit-only command, never the `mp_runner` URLs) and leaves both unset when data.env lacks them (§3). Outside mp-data they are the `mp_agent` URLs of the env file.
  - `secret:<ref>`: the client's server; resolves engagement `secrets.<ref>` (inside the jail from `MP_SECRET_SECRETS_<REF>`, §2), takes the dialect from the URL scheme, refuses `--dialects` and `--replace`, and never prints or logs the URL or any part of it. The target must be an empty database the client provides.
  - a literal URL (`postgresql://...` or `mssql://...`, one database, dialect from the scheme, `--dialects` refused): only in non-data-plane commands such as `uv run toolkit stage1 <eng> --target-db "$PG_URL/{{e.db_name}}_dev" --schema-from auto --schema-only --replace`.
  - With `--dataset engagement`, `fresh-copy` or a `DIR` under `{{p.private}}` (all engagement data) and without `--schema-only`, a `local:` name must start with `mpdata_` and a literal URL is refused (exit 2, `engagement data loads only into mpdata_* databases or a secret: target`). mp-data is stricter: it refuses a literal URL, or a `local:` name without `mpdata_`, in every stage1 call.
  - An existing `mpdata_` database whose owner is not `postgres`, `sa` or `mp_runner` exits 2 before anything is created, loaded or checked (`mpdata_ database <name> is owned by <role>, not postgres, sa or mp_runner: drop it as the superuser`; §1 Databases). A database stage1 creates belongs to the superuser in its URL.
- `stage1` modes:
  - Default (no `--replace`, no `--verify`): creates the database if it is missing (`local:` and URL targets, never `secret:`); when the target has no tables, creates the schema and loads; when tables and row counts already match, does nothing; otherwise exits 1 `target differs; use --replace` (`secret:` targets: `target not empty; ask the DBA to drop and recreate it`).
  - `--replace` (`local:` and URL targets only): creates the database if missing, drops every table in its default schema (`public` on PostgreSQL, `dbo` on SQL Server; foreign keys first), creates the schema and loads. It never drops the database.
  - `--schema-only`: DDL only; reads only the design files (`model/schema.json`, or `analysis/data-model.json` with `--schema-from data-model`) and the engagement, never a dataset or another data-zone path, so S4.models runs it with `uv run toolkit`. It combines with the default mode, `--replace` and `--verify`.
  - `--verify`: after the load when combined with `--replace`; alone it is read-only and never creates, loads or changes anything (a missing database or table is a failed check). Checks: tables, columns and mapped types; keys, unique indexes and foreign keys; row counts; per-table sha256 over the sorted canonical rows (§7); no foreign-key orphans; identities past the maximum. With `--schema-only` only the structure is checked.
  - `--require-rows`: exit 1 when a target table has fewer rows than the dataset or every dataset table is empty.
  - `--dataset fresh-copy` (client cutover and go-live only) reads the back-end file at `database.stage1.fresh_copy` through `toolkit.dataset`'s back-end reader.
  - `--report FILE` writes Markdown with counts, digest-match flags and sha256s of the inputs, no values and no timestamps, so two runs on the same data give the same bytes.
- `relink-scan --check` renders in memory and compares with `--out`: exit 1 when the bytes differ or the file is missing (naming the sections that differ); it never writes.
- `results summarize`: required cells from the engagement and the manifest; each required file must be in exactly one `--junit-dir` (§12). S6.report's verify writes to `{{p.private}}/review/$MP_DB_SUFFIX/results.json` and compares it with `cmp`.
- `purge`: runs only as `mp-data purge`, with the superuser URLs (a toolkit-only command, §3); it finds each local server through stage1's `local:` server resolution. It deletes everything under `{{p.private}}` (directories that are mount points in the jail, `{{p.private}}` included, stay, empty) and drops `mpdata_{{e.db_name}}_*` (the Stage 1 load, the gate import), `mpdata_test_{{e.db_name}}_*`, `{{e.db_name}}_dev`, `{{e.db_name}}` and every `test_{{e.db_name}}_*` on every running local dialect (PostgreSQL, and SQL Server when its container answers). It matches names in Python with exact prefixes, never with SQL `LIKE` (where `_` is a wildcard). A name that matches these patterns but belongs to another engagement whose `db_name` begins with this `db_name` plus `_` is kept: the jail hides the other engagements, so mp-data passes their db_names in `MP_OTHER_DB_NAMES` (comma-separated, computed before the jail starts from every other engagement.json under `$HOME/engagements/*/record/` and `{{p.pipeline}}/engagements/`, `_template` excluded, each db_name derived as in §1; purge exits 2 when it is unset, and an empty value means none). A dialect without a server URL, or whose server does not answer, is named as not checked, with the reason. It writes `{{p.records}}/destruction-record-<YYYY-MM-DD>.md` (§15), listing every `environment.windows_box.copies` entry to delete by hand. Its declared outputs in the jail are `{{p.private}}` itself and `{{p.records}}` (§3). It refuses (exit 2) when `{{p.private}}` is a symlink or does not end in `/engagements/<id>/private`. `--dry-run` prints the same plan as counts, sizes and database names and deletes and writes nothing.
- `wireframe --check`, `wireframe-xlsm --check`: render in memory and compare with `--out`; exit 1 on any missing, extra or changed `.svg` or `.md` file (`wireframe-xlsm` ignores `.png` files); write nothing. A normal run deletes the `.svg` files (xlsm: and `.png`) in `--out` that it did not produce. Both read layout only from the design-zone model (§4).

<a id="scripts"></a>
Scripts (S0.workspace):

| Script | Form |
|---|---|
| `scripts/mp-data` | bash (reads the engagement and `paths.local.json` with jq; hands over to `toolkit/dataplane.py`); installed as `$HOME/.local/bin/mp-data` (a copy, mode 755, never a symlink); forms in §3 |
| `scripts/mp-run` | bash; installed as `$HOME/.local/bin/mp-run`; §17 |
| `scripts/mp-bash-hook` | `python3 -I`, stdlib only; installed as `$HOME/.local/bin/mp-bash-hook`; §17 |
| `scripts/client-settings.sh` | `client-settings.sh <id>` (the only argument; also runs piped as `git show origin/main:scripts/client-settings.sh \| bash -s -- <id>`): bash, jq and git (git for the repos' git common directories); prints the client-session settings JSON on stdout (§17) |
| `scripts/install-mp-data.sh` | copies mp-data, mp-run and mp-bash-hook from its own checkout into `$HOME/.local/bin` (mode 755, never symlinks) and creates `$HOME/.local/share/modernization/`; idempotent; run by the `mp_data` environment item and by `mp-data pin`. The `mp_data` check tests only that the copies are in place (and, for clients, equal the pinned release's), so after a merge that changes one of the three scripts, run it again from an up-to-date main checkout. |
| `scripts/dev_db.sh` | Agent databases only. `createdb <name>` and `dropdb <name>` (name `^[a-z][a-z0-9_]{1,62}$` and not starting with `mpdata_`, else exit 2 before any docker call) act on each dialect whose URL the env file sets and whose container runs, as `mp_agent` inside the container (`docker exec ... createdb -h 127.0.0.1 -U mp_agent`, or sqlcmd as `mp_agent`, the password from `PG_URL` or `MSSQL_URL` in that call's environment only), so `mp_agent` owns the database; createdb leaves an existing database unchanged and dropdb ignores a missing one. `url pg\|mssql` prints `PG_URL` or `MSSQL_URL` (exit 1 when unset). Any other subcommand, `up` and `check` included, exits 2: provisioning (containers, roles, passwords, `pg_hba.conf`) lives only in the `postgres` and `mssql` environment items. It reads only the env file, never data.env, and needs Docker, which mp-run hides, so it runs in plain-terminal steps (the S4 and S5 gates create the app dev database with it); S0's tests run it with a stub `docker` on PATH. |

There is no `scripts/git-credential-mp`: client target pushes go through `mp-data push` (§3).
