# Orchestrator runbook

**Status:** v2, reviewed 2026-10-16. Nothing here has been run yet.
**Who follows it:** the orchestrating session, a Claude Code session that works with the user. It runs one stage for one engagement at a time by calling `.claude/workflows/run-stage.js` with the Workflow tool, through a per-stage launcher that `.claude/workflows/make-launcher.py` writes (section 2(f)).
**Related files (in the pipeline repo):** `docs/proposal.md` (why), `docs/contracts.md` (formats, CLI contracts, placeholders, command forms), `pipeline.json` (stages, tasks, environment list), `engagements/<id>/engagement.json`.

## Rules for the orchestrating session
- **Ask the user before every GitHub action:** creating a repo, opening or merging a PR, deleting a branch or a workflow run, changing visibility. One question may cover the actions of one gate ("record, push and merge?").
- **Your shell resets between Bash calls.** Every block below starts with `. $HOME/engagements/<id>/run.env`, which sets the variables, puts `$HOME/.local/bin` first on PATH and defines `rgit` (1.3). Replace `<id>` by hand. Anything else in `<angle brackets>` is also yours to fill; `<PIPE>`, `<TREPO>`, `<TARGET>`, `<PRIV>`, `<REC>` and `<ENVF>` stand for the absolute values in run.env.
- **Long commands** (downloads, prepare lines, CI waits) run as background Bash calls. You are notified when they exit. Never use a foreground `sleep`.
- **Zones.** The design zone is `$PRIV/out/`, `$PRIV/review/` and `$PRIV/synthetic/`. Everything else under `$PRIV` is the data zone: `sources/`, `datasets/`, `workbook/`, `reference/`, `goldens/`, `corpus/`, `results/`, `logs/`, `assets/` and `scrub-map.json`. Never open, list, cat or query it, and never read `$HOME/.config/modernization/data.env`. Only `mp-data` does. Never paste data values or secrets into prompts, files, PRs or chat.
- **Run commands in the form you are given.** Run the dry run's `prepare` and `exit_criteria` lines exactly as it returns them (section 2(f)), each as its own Bash call. In a session, write your own mp-data calls as standalone data-plane calls with the paths typed out: `mp-data [--slot <slot>] <command> <REC>/engagement.json ...`, with no `&&`, `;`, pipe, redirection or `$`. Only blocks marked for a plain terminal combine mp-data with other shell. Never run mp-data inside mp-run: mp-run hides data.env, and mp-data's own bwrap jail cannot start inside mp-run's.
- **Environment commands are never wrapped** in mp-run or anchored: they need Docker and data.env. In demo sessions you run them yourself; for clients the user runs them in a plain terminal (section 2(c)).
- **Plain-terminal gate steps.** Gate steps that touch Docker, data.env, the data zone or `mpdata_` databases are done by the user in a plain terminal outside Claude, for demos and clients. Give the user the commands from the checklist; never run them yourself.
- **Plain-terminal git on client repos.** For a client engagement, every git command in a plain-terminal block runs as `rgit <dir> ...` (1.3), on the records repo, the pipeline clone and worktree, and the target repo. rgit turns repo hooks and fsmonitor off (`-c core.hooksPath=/dev/null -c core.fsmonitor=false`), because agents can write the pre-commit cache those hooks run. Hooks run only in commits made inside mp-run (integrate, checkpoint and your own session blocks).
- **Keep the trees clean before a run.** Commit your own edits (records, answers, dependencies) first. The driver blocks the commit when it finds changes outside owned paths.
- **Use absolute paths.** `args.paths` and `paths.local.json` hold expanded paths, never `~`.
- **Client work never runs in a cloud session.** Client sessions start with the generated client-session settings and follow section 1.9.
- **Public demo repo.** In every demo stage, public or not, integrate and checkpoint both run `mp-data --slot <slot> guard <REC>/engagement.json --repo <PIPE> --repo <TREPO> --history` before they push. Once the repo is public (section 6), you run `mp-data guard <REC>/engagement.json --repo <PIPE> --repo <TREPO> --history` before every push you make to it.

---

## 0. Setup (once per machine)

### 0.1 Create the two repos
Log in with gh's file-stored token over HTTPS: `gh auth login --insecure-storage` (choose HTTPS), then `gh config set git_protocol https` and `gh auth setup-git`; `gh auth status` must name `hosts.yml`.

Ask the user before creating the repos. Each command clones into `$HOME/sourcecode`.
```
mkdir -p $HOME/sourcecode && cd $HOME/sourcecode && gh repo create thomas-sodon/modernization-pipeline --private --add-readme --clone
cd $HOME/sourcecode && gh repo create thomas-sodon/access-excel-to-django --private --add-readme --clone
```
`--add-readme` gives both repos a `main` branch, which every stage branch starts from.

### 0.2 Copy the plan into the pipeline repo
```
PF=/home/cloudcli/sourcecode/scratch-pad/docs/modernization-pipeline
BP=/home/cloudcli/sourcecode/scratch-pad/docs/software-modernization-viability.md
PIPE=$HOME/sourcecode/modernization-pipeline
mkdir -p "$PIPE/docs" "$PIPE/.claude/workflows" "$PIPE/tests/driver"
cp "$PF/proposal.md" "$PF/contracts.md" "$PF/runbook.md" "$PIPE/docs/"
cp "$BP" "$PIPE/docs/business-plan.md"
cp "$PF/pipeline.json" "$PIPE/pipeline.json"
cp -r "$PF/engagements" "$PIPE/engagements"
cp "$PF/run-stage.workflow.js" "$PIPE/.claude/workflows/run-stage.js"
cp "$PF/make-launcher.py" "$PIPE/.claude/workflows/make-launcher.py"
cp "$PF/tests/run-stage.test.mjs" "$PIPE/tests/driver/run-stage.test.mjs"
sed -i 's#\.\./software-modernization-viability\.md#business-plan.md#g' "$PIPE/docs/proposal.md"
sed -i -e 's#](pipeline\.json#](../pipeline.json#g' -e 's#](engagements/#](../engagements/#g' -e 's#](run-stage\.workflow\.js)#](../.claude/workflows/run-stage.js)#g' -e 's#](tests/run-stage\.test\.mjs)#](../tests/driver/run-stage.test.mjs)#g' "$PIPE/docs/proposal.md" "$PIPE/docs/contracts.md"
```
Check that no relative link is broken (prints nothing when all are fine). This runbook has no file links, and its own command text would confuse both commands, so it is left out:
```
cd $HOME/sourcecode/modernization-pipeline/docs && grep -o '](\([^)]*\))' proposal.md contracts.md business-plan.md | sed 's/^[^:]*:](\(.*\))$/\1/; s/#.*//' | grep -v -E '^(https?:|mailto:|$)' | sort -u | while read -r l; do test -e "$l" || echo "broken link: $l"; done
```

### 0.3 Minimal repo files
S0.workspace owns `.gitignore`, `.python-version` and `CLAUDE.md` and extends them in S0. Write minimal versions now:
```
cd $HOME/sourcecode/modernization-pipeline && printf '3.13\n' > .python-version && cat > .gitignore <<'EOF'
/vendor/
/private/
/data/
__pycache__/
*.pyc
.venv/
.pytest_cache/
.ruff_cache/
paths.local.json
EOF
```
Source-file extensions are not ignored here on purpose: synthetic test fixtures under `toolkit/tests/fixtures/` may use them. The forbidden-file hooks and the guard (built in S0) stop real source files, and the first S0 checkpoint checks staged names against the same list.

Write `CLAUDE.md` with this content:
```
# modernization-pipeline

Tools, templates and the stage driver that turn an MS Access or Excel VBA app into a Django app
that is proven to behave the same. Read docs/proposal.md, docs/contracts.md and docs/runbook.md.

- Each stage is one run of .claude/workflows/run-stage.js for one engagement. Workflow tasks never commit.
- Private engagement data lives in $HOME/engagements/<id>/private and is never committed.
  You may read only the design zone: private/out/, private/review/ and private/synthetic/.
  Everything else under private/ is the data zone: only `mp-data <command>` reads it.
- The env file is $HOME/.config/modernization/env (PATH, UV_PYTHON_PREFERENCE, JAVA_HOME, PG_URL,
  MSSQL_URL, LD_LIBRARY_PATH, PLAYWRIGHT_BROWSERS_PATH). It holds no secrets. Never print it.
  Secrets live in $HOME/.config/modernization/data.env: never read it.
- Use uv only. Python is 3.13 (uv-managed). There is no sudo.
- Your shell resets between calls: run each command in the form your prompt gives it
  (`mp-run --eng ... --slot ... -- '<command>'`, a standalone `mp-data` call, or in the first S0 the braced form).
- Database containers: mp-pg16 (127.0.0.1:55432) and mp-mssql22 (127.0.0.1:14330).
  PG_URL and MSSQL_URL log in as mp_agent, which every mpdata_ database refuses.
```

### 0.4 Check the driver
```
cd $HOME/sourcecode/modernization-pipeline && node tests/driver/run-stage.test.mjs
```
It must exit 0. It runs dry runs of every stage for both demos and prints the agent estimates.

### 0.5 Commit and push main
Ask the user first. Use the commit attribution lines this session was given as extra `-m` paragraphs.
```
cd $HOME/sourcecode/modernization-pipeline && git add .gitignore .python-version CLAUDE.md docs pipeline.json engagements .claude/workflows/run-stage.js .claude/workflows/make-launcher.py tests/driver/run-stage.test.mjs && git commit -m "Seed the pipeline repo from the v2 plan" -m "<attribution lines>" && git push origin main
```
The demo repo's root `README.md` and `NOTICE.md` hold one section per engagement between the marker lines `<!-- engagement:<id> -->` and `<!-- /engagement:<id> -->` (docs/contracts.md section 1). Create both now with empty sections; each engagement's S6.handover fills only its own:
```
cd $HOME/sourcecode/access-excel-to-django && printf '%s\n' '# access-excel-to-django' '' 'Demo engagements of the modernization pipeline: an MS Access app and an Excel VBA app rebuilt as Django apps, each with equivalence tests against the original. Each section below belongs to one engagement and is written at its S6 stage.' '' '<!-- engagement:northwind-demo -->' '<!-- /engagement:northwind-demo -->' '' '<!-- engagement:invoicegen-demo -->' '<!-- /engagement:invoicegen-demo -->' > README.md && printf '%s\n' '# Notices' '' 'Third-party material and credits, one section per engagement, written at its S6 stage.' '' '<!-- engagement:northwind-demo -->' '<!-- /engagement:northwind-demo -->' '' '<!-- engagement:invoicegen-demo -->' '<!-- /engagement:invoicegen-demo -->' > NOTICE.md && git add README.md NOTICE.md && git commit -m "Root README and NOTICE with one section per demo engagement" -m "<attribution lines>" && git push origin main
```

### 0.6 Environment for S0
The runbook's own blocks use jq 1.7 or later. On a machine without it, install the pinned binary once (the same commands as the `jq` environment item's fix):
```
PATH=$HOME/.local/bin:$PATH; jq --version 2>/dev/null | grep -Eq '^jq-1\.(7|8)' || { mkdir -p $HOME/.local/bin && d=$(mktemp -d) && curl -fsSL -o "$d/jq" https://github.com/jqlang/jq/releases/download/jq-1.8.1/jq-linux-amd64 && echo "020468de7539ce70ef1bceaf7cde2e8c4f2ca6c3afb84642aabc5c97d9fc2a0d  $d/jq" | sha256sum -c - && test "$(stat -c %s "$d/jq")" = 2255816 && chmod 755 "$d/jq" && mv "$d/jq" $HOME/.local/bin/jq && rm -rf "$d"; }
```
Then do 0.7 first (the `workflow_size` item checks its marker and has no fix), section 1.1 to 1.4 for `northwind-demo`, the first command of section 2(a) with `S=S0`, and section 2(c). In the first S0 the items `mp_data` and `hooks` are skipped (their `when` is false until S0 has merged once), and section 2(g) skips the S0 prepare list because mp-data is not installed yet. `bwrap` must pass from the first S0: S0.workspace tests mp-run with it, mp-data runs its toolkit child under it, and the driver runs agents' commands through mp-run from S1 on.

### 0.7 Workflow size
Ask the user to open `/config` and raise **Dynamic workflow size** so one workflow may run at least 20 agents. Then:
```
mkdir -p $HOME/.config/modernization && touch $HOME/.config/modernization/workflow-size-raised
```
The `workflow_size` environment item checks this marker. This machine runs 6 agents at a time; larger fan-outs queue.

### 0.8 Start the stage session
Every stage runs in a session whose working directory is the pipeline checkout, so subagents get its `CLAUDE.md` and hooks and their shells reset into it. Source the env file first: it puts `$HOME/.local/bin` on PATH, so `mp-data` and `mp-run` resolve as bare words in every Bash call, as the dry run's commands need. Add the target repo and the engagement directory, so agents can write there without permission prompts:
```
. $HOME/.config/modernization/env && cd $HOME/sourcecode/modernization-pipeline && claude --permission-mode auto --add-dir $HOME/sourcecode/access-excel-to-django --add-dir $HOME/engagements/northwind-demo
```
Workflow agents run many distinct Bash commands, which the default mode asks about one by one; without auto mode on your plan, use `--allowedTools 'Bash(mp-run:*)' 'Bash(mp-data:*)'` instead and approve the braced commands of the first S0 as they come.

Run demos on this machine, and continue an unfinished stage on the machine where it started: the stages need Docker, bwrap and the stage's private directory, and the first S0 installs mp-data and the git hooks here through S0.workspace's acceptance, which a reused S0.workspace does not run again.

In the new session, before the first real run, write a file `<target repo>/.mp-probe` with the Write tool and delete it. If Claude Code asks for permission, the session is missing an `--add-dir`: restart it.

Start a new session whenever you switch engagement, because `--add-dir` names one engagement directory.

### 0.9 Another machine (for example a client host)
Log in to gh as 1.6 says (a fine-grained token limited to `thomas-sodon/modernization-pipeline`: `gh auth login --with-token --insecure-storage`, then `gh config set git_protocol https` and `gh auth setup-git`) and set `git config --global user.name` and `user.email`. Then skip 0.1 to 0.5 and clone instead (the demo repo only if demos run there):
```
mkdir -p $HOME/sourcecode && cd $HOME/sourcecode && gh repo clone thomas-sodon/modernization-pipeline
```
Then install jq if needed (0.6), do 0.7, and run the environment per stage as in 2(c). `bwrap` has no automatic fix: if its check fails, ask the user to run `sudo apt-get install bubblewrap`. On Ubuntu 24.04 and later, when the check still fails and `sysctl kernel.apparmor_restrict_unprivileged_userns` prints 1, bwrap needs an AppArmor profile named bwrap that allows user namespaces. Look for one first: `grep -l '^profile bwrap ' /etc/apparmor.d/*` (Ubuntu 26.04 ships it in `/etc/apparmor.d/bwrap-userns-restrict`; reload it with `sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict`). Only when no file defines it, write the profile that the item's note in pipeline.json gives and run `sudo systemctl reload apparmor`; never define a second profile named bwrap. socat is not needed.

---

## 1. Per engagement (once)

### 1.1 Engagement id
- Demos: `northwind-demo`, `invoicegen-demo`. invoicegen-demo runs S0, S1, S2, S4, S5 and S6 (S3 does not apply to a workbook).
- Clients: an opaque id `c<YYYY><NNN>`, for example `c2026001`. The client's name appears only in the records. Find the last one used this year:
```
ls -d $HOME/engagements/c$(date -u +%Y)[0-9][0-9][0-9] 2>/dev/null | sort | tail -1
```
Add 1 (start at 001).

### 1.2 Directories
```
mkdir -p $HOME/engagements/<id>/private && chmod 700 $HOME/engagements $HOME/engagements/<id>
```
The private directory is the same path for every stage and session. When it is lost, each stage's `prepare` list rebuilds it.

### 1.3 run.env
One file of shell variables per engagement, outside every repo. The first heredoc expands `$HOME` when it is written, so the values are absolute; `\$PATH` stays literal.

northwind-demo:
```
cat > $HOME/engagements/northwind-demo/run.env <<EOF
ID=northwind-demo
PIPE=$HOME/sourcecode/modernization-pipeline
PIPE_GH=thomas-sodon/modernization-pipeline
TREPO=$HOME/sourcecode/access-excel-to-django
TREPO_GH=thomas-sodon/access-excel-to-django
TMODE=github
TOKEN_REF=
TARGET=$HOME/sourcecode/access-excel-to-django/northwind
TPFX=northwind/
PRIV=$HOME/engagements/northwind-demo/private
REC=$HOME/sourcecode/modernization-pipeline/engagements/northwind-demo
ENVF=$HOME/.config/modernization/env
PATH=$HOME/.local/bin:\$PATH
EOF
```
invoicegen-demo: the same with `invoicegen-demo` for the id and the paths, `TARGET=$HOME/sourcecode/access-excel-to-django/invoice-generator` and `TPFX=invoice-generator/`.

Client (`c2026001` as the example):
```
cat > $HOME/engagements/c2026001/run.env <<EOF
ID=c2026001
PIPE=$HOME/engagements/c2026001/pipeline
PIPE_GH=thomas-sodon/modernization-pipeline
TREPO=$HOME/engagements/c2026001/target
TREPO_GH=<client-org/repo from target.repo; empty when target.mode is local>
TMODE=<github | local>
TOKEN_REF=<secrets.github_push.ref, for example env:GITHUB_PUSH_C2026001; empty when target.mode is local>
TARGET=$HOME/engagements/c2026001/target
TPFX=
PRIV=$HOME/engagements/c2026001/private
REC=$HOME/engagements/c2026001/record
ENVF=$HOME/.config/modernization/env
PATH=$HOME/.local/bin:\$PATH
EOF
```
Then append the `rgit` function, the same for every engagement (the quoted heredoc writes it as it is):
```
cat >> $HOME/engagements/<id>/run.env <<'EOF'
rgit() { d=$1; shift; if [ "$d" = "$TREPO" ] && [ -n "$TOKEN_REF" ] && { [ "$1" = fetch ] || [ "$1" = ls-remote ]; }; then MP_GIT_TOKEN=$(case "$TOKEN_REF" in env:*) sed -n "s/^export ${TOKEN_REF#env:}=//p" $HOME/.config/modernization/data.env | tail -n 1 ;; pass:*) pass show "${TOKEN_REF#pass:}" | head -n 1 ;; esac) GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 git -C "$d" -c core.hooksPath=/dev/null -c core.fsmonitor=false -c credential.helper= -c 'credential.helper=!f() { test "$1" = get && echo username=x-access-token && echo "password=$MP_GIT_TOKEN"; }; f' "$@"; else git -C "$d" -c core.hooksPath=/dev/null -c core.fsmonitor=false "$@"; fi; }
EOF
```
Rules: `TARGET` is `TREPO` plus `/` plus `target.root` (just `TREPO` when the root is `.`); `TPFX` is `target.root/` (empty when the root is `.`). `rgit <dir> <git args>` is `git -C <dir>` with repo hooks and fsmonitor off; use it for every git command you run in a plain terminal on a client engagement's repos (rules). On a client target repo (`TOKEN_REF` set), `rgit "$TREPO" fetch ...` and `rgit "$TREPO" ls-remote ...` also hand the push token to git for that one call through a one-shot credential helper and store nothing. For those two calls git reads only the repo's own config (no `~/.gitconfig`, no system config), as `mp-data push` does, so a global `url.*.insteadOf` can neither send the token to another host nor switch the fetch to SSH. The token comes from data.env (read literally, as mp-data reads it) or `pass`, so this works only in a plain terminal; inside mp-run and in sessions the token is unreadable. Pushes and PRs on a client target repo go through `mp-data push` and `mp-data gh` (section 2(i)). Section 2(a) appends `S=` and `SLUG=` lines.

### 1.4 Records
- **Demos:** `$PIPE/engagements/<id>/` in the pipeline repo, committed on the stage branches. Nothing to do.
- **Clients:** a local git repo of its own, never pushed, never in the pipeline repo, never in a cloud session. Create it before S0: S0.workspace writes `log.md` and `source.lock.json` there, and the driver commits the records repo.
```
. $HOME/engagements/<id>/run.env && mkdir -p "$REC" && rgit "$REC" init -b main && printf 'paths.local.json\n' > "$REC/.gitignore" && rgit $HOME/sourcecode/modernization-pipeline show origin/main:engagements/_template/engagement.json > "$REC/engagement.json"
```
Fill every `<...>` marker in `$REC/engagement.json` with the user (id, records path, target, app, database, auth, hosting, consent, secrets refs such as `github_push`, `staging_db_url_ref` and `stage1_target_url_ref`, scope, commercial with its milestones). Leave `null` values alone. Then commit:
```
. $HOME/engagements/<id>/run.env && rgit "$REC" add .gitignore engagement.json && rgit "$REC" commit -m "$ID: engagement record"
```

### Client-only steps (1.5 to 1.13)
Prerequisite for any client delivery: the pipeline release used includes both demos through S6. The purge, report and wireframe tools are built in the S6 bootstrap, so main must hold the S6 gate of `northwind-demo` and `invoicegen-demo` (section 1.12 checks it).

### 1.5 Pipeline worktree
One pipeline worktree per active engagement, so two engagements never switch branches under each other:
```
. $HOME/engagements/<id>/run.env && rgit $HOME/sourcecode/modernization-pipeline fetch origin && rgit $HOME/sourcecode/modernization-pipeline worktree add --detach "$PIPE" origin/main
```
Merge each engagement's toolkit changes to main through its stage PR before another engagement's next stage starts.

### 1.6 Target repo
**Secrets.** A secret value (the client's push token here; later the Stage 1 target database URL for the S3 cutover, the staging database URL for the S5 gate and the production database URL for the S6 go-live load) lives only in data.env or in `pass`, never in the env file, a repo, a prompt or the shell that starts a session. The engagement names it by a ref in `secrets` (`secrets.github_push.ref`, `stage1_target_url_ref`, `staging_db_url_ref`, `production_db_url_ref`; docs/contracts.md section 2). In a plain terminal, store it under that ref:
- `env:NAME` (NAME matches `[A-Z_][A-Z0-9_]*`, for example `GITHUB_PUSH_C2026001`). Type the value at the silent prompt. The command replaces an earlier line for NAME (a renewed token):
```
umask 077 && D=$HOME/.config/modernization/data.env && mkdir -p "${D%/*}" && touch "$D" && chmod 600 "$D" && read -rs V && case "$V" in ''|*[!A-Za-z0-9_.:/@%+=,-]*) unset V; echo 'Not stored: empty, or a character outside A-Za-z0-9 _ . : / @ % + = , - (use pass instead)'; false ;; esac && sed -i '/^export <NAME>=/d' "$D" && printf 'export %s=%s\n' '<NAME>' "$V" >> "$D" && unset V
```
  mp-data reads the text after `=` literally, and gate steps also source data.env, so a value must be safe both ways. A value with any other character (for example a URL with `?` or `&` in its query) goes in `pass`.
- `pass:<path>`: `pass insert <path>`.

**target.mode github.** The client created the repo with a `main` branch and gave a fine-grained token (or GitHub App) scoped to it. Store it as above under the ref you put in `secrets.github_push.ref` and in run.env `TOKEN_REF`. Then create the clone with no stored credentials, and fetch it with the token for that one call:
```
. $HOME/engagements/<id>/run.env && mkdir -p "$TREPO" && rgit "$TREPO" init -b main && rgit "$TREPO" remote add origin "https://github.com/$TREPO_GH.git" && rgit "$TREPO" config credential.helper '' && rgit "$TREPO" config credential.useHttpPath true
. $HOME/engagements/<id>/run.env && rgit "$TREPO" fetch origin && rgit "$TREPO" checkout -B main origin/main
```
`origin` (url and pushurl) must be exactly `https://github.com/<target.repo>` (with or without `.git`), where `target.repo` is the GitHub owner/name. The empty `credential.helper` resets every global helper for this repo, so `gh auth git-credential` or a stored password never answers for it; `useHttpPath` keeps any helper per repo. Set no `url.*.insteadOf`, `url.*.pushInsteadOf`, `remote.origin.proxy` or `http.*` proxy or ssl key in the repo's own git config: `mp-data push` refuses such a repo (exit 2), and `mp-data push` and `mp-data gh` both refuse any other `origin`. Never fetch the client repo with plain `git`. The token reaches git only through `rgit` in a plain terminal (fetch, ls-remote) and through `mp-data push` and `mp-data gh` (push, pull requests, checks, merge, Actions runs), which read the ref themselves and never print it. Agents and mp-run cannot read it.

Your `gh` login (environment item `gh`) is for the pipeline repo only. On a client host, log in with a fine-grained token limited to `thomas-sodon/modernization-pipeline`, so it cannot reach client orgs, and store it in gh's file (`gh auth login --with-token --insecure-storage`, token on stdin, then `gh auth setup-git` so git pushes the pipeline repo with it), because mp-run hides the keyring (1.9).

**target.mode local** (health check, assessment):
```
. $HOME/engagements/<id>/run.env && mkdir -p "$TREPO" && rgit "$TREPO" init -b main && rgit "$TREPO" commit --allow-empty -m "$ID: target repo"
```
`target.local_path` in engagement.json must equal `$TREPO`.

When the client later buys a stage that delivers code (stage1 or stage2; the driver refuses those stages in local mode): ask the client to create the repo empty (no README, licence or .gitignore). Store their token as above; set `target.mode` `github`, `target.repo`, `target.local_path` `null`, `secrets.github_push`, `commercial.tier`, `hosting.provider` and `managed_by`, and `database.stage1` when stage1 is sold, in engagement.json; set `TREPO_GH`, `TMODE` and `TOKEN_REF` in run.env; add their repo as `origin` with the two `credential` lines above. If the repo already has commits, run `rgit "$TREPO" fetch origin && rgit "$TREPO" rebase origin/main` first. Then push `main` only: `mp-data push <REC>/engagement.json --branch main` (the local stage branches were deleted at their local merges). In the next 2(a), the target count for stages merged while local is 0 by design.

### 1.7 Client files
The user, not the session, copies the client's files into the data zone from a plain terminal:
```
install -d -m 700 $HOME/engagements/<id>/private/sources $HOME/engagements/<id>/private/sources/data && install -m 600 <file the client sent> $HOME/engagements/<id>/private/sources/<design file name>
```
For a data copy or a back end:
```
install -m 600 <file> $HOME/engagements/<id>/private/sources/data/<file name>
```
(The S3 cutover directory `sources/cutover/` is created at the S3 client gate.) Record each file's path in `source.path`, `source.data_copy` or `source.backends`. Encrypted or password-protected files are a custom quote for now (no codec is pinned and parse exits 2 with "encrypted source: custom quote"): ask the client for an unencrypted copy.

### 1.8 Host marker
Create it only on machines approved for client data (encrypted disk, your own hardware):
```
mkdir -p $HOME/.config/modernization && touch $HOME/.config/modernization/client-host
```
Before each client run: `test -f $HOME/.config/modernization/client-host` must pass. Then pass `host_attested: true`.

### 1.9 Where client commands run
Client sessions run without the Claude Code sandbox (a sandboxed command cannot reach the database containers on the host's 127.0.0.1, and Docker does not work in it). Instead the settings from 1.10 deny Read, Edit and Write on this engagement's data zone, on every other engagement's `private/` and `record/` and on data.env, and a PreToolUse hook (`$HOME/.local/bin/mp-bash-hook`) blocks every Bash call that is not exactly one `mp-run` or `mp-data` call: one line, no `&&`, `;`, pipe, parenthesis or redirection outside quotes, no `$` or backtick outside single quotes, no variable assignment in front (docs/contracts.md section 17). So a session's mp-data calls have every path and value typed out (`"$REC"` is refused there); inside mp-run's single-quoted command, variables are fine. The hook covers workflow agents too. These means stop accidental exposure of client data by agents that follow their prompts; they do not contain a deliberately hostile agent. The recorded residual risks (docs/contracts.md section 1, threat model) are mostly in your plain terminal: dotfiles and git and gh config that agents can write, agent-written app code you run at the S5 client gate, and the caches the `hooks` and `chromium` fixes use.
- **Plain terminal, outside Claude:** sections 1.2 to 1.12 (they come before the first session; 1.6, 1.7, 1.10 and 1.12 also need data.env, the token or a terminal); per stage 2(a), 2(c) and 2(d); change-order merges into a later stage branch (5, step 4); every gate step that touches Docker, data.env, the data zone or `mpdata_` databases (the S3 relink rehearsal and cutover, the S5 matrix counts, the S5 import rehearsal into `mpdata_<db_name>_gate` as `mp_runner`, the S5 staging load, purge). Git runs there only as `rgit` (rules).
- **In the session:** everything else. Run a runbook block as one mp-run call with the slot `orchestrator`:
```
mp-run --eng <REC>/engagement.json --slot orchestrator -- '<block>'
```
  Inside the quotes, write each single quote as `'\''`. A block with single quotes or several lines is easier as a file: save it with the Write tool as `$HOME/engagements/<id>/ops/<name>.sh` and run `mp-run --eng <REC>/engagement.json --slot orchestrator -- 'bash <absolute path of that file>'`. mp-run sources the env file, exports `MP_DB_SUFFIX=<slot>` and `MP_SANDBOX=mp-run`, unsets `DOCKER_HOST`, changes to the pipeline worktree unless the command starts with `cd `, and runs the command under bwrap. There the data zone, other engagements, data.env, the pass store, the Docker sockets and the sockets of your own processes (`$XDG_RUNTIME_DIR` with the session bus, keyrings and ssh and gpg agents, and tmux and screen sockets) are hidden; the installed tools (`$HOME/.local/bin`, `$HOME/.local/share/modernization`, `$HOME/.local/share/uv`), `$HOME/.config/modernization`, the Claude Code settings (`$HOME/.claude`, `$HOME/.claude.json`, the worktree's `.claude/` and `.mcp.json`, this engagement's client-session.json) and the git hooks and git config of the pipeline and target repos are read-only. The network is kept, so git, `gh` (your pipeline login) and uv work. Because the keyring is hidden, git and gh there use gh's file-stored token: log in with `gh auth login --insecure-storage`, so that `gh auth status` names `hosts.yml`, not `keyring`. This holds for demos too, whose integrate and checkpoint agents push through mp-run from S1 on.
- **mp-data calls** run directly, each as its own Bash call, never inside mp-run: the dry run's data-plane lines, and `mp-data push` and `mp-data gh` for the target repo. mp-data runs the pinned release (1.12) and starts its toolkit child in a bwrap jail of its own: the child can write only this engagement's data zone, `$TMPDIR` and the call's declared outputs, never sees data.env, the pass store, Docker or the sockets of your own processes, and gets the `mp_runner` database login when it runs agent-written code (`equivalence`, `reference`, `golden record`) and the superuser for `stage1` and `purge`.

### 1.10 Client-session settings
S0.workspace builds `scripts/client-settings.sh` (docs/contracts.md section 17). It prints the settings for one engagement with absolute paths: `disableAllHooks` false, sandbox off, bypass-permissions mode disabled, Read, Edit and Write denied as in 1.9, Edit and Write denied on `$HOME/.local/bin`, `$HOME/.local/share/modernization`, `$HOME/.config/modernization`, `$HOME/.claude`, `$HOME/.claude.json` and the settings file itself, and (once `$REC/paths.local.json` exists) on the worktree's `.claude/` and `.mcp.json` and the git hooks and git config of the pipeline and target repos, Read denied on `/proc`, `Monitor` denied, the Bash hook, and `Bash(mp-run:*)` and `Bash(mp-data:*)` allowed (the hook still refuses every other shape first). Generate it from main in a plain terminal before every session start, because its deny list names the engagements that exist at that moment (run 2(a) first, which fetches main, and 2(d), which writes paths.local.json):
```
. $HOME/engagements/<id>/run.env && test -x $HOME/.local/bin/mp-bash-hook && F=$HOME/engagements/$ID/client-session.json && rgit $HOME/sourcecode/modernization-pipeline show origin/main:scripts/client-settings.sh | bash -s -- "$ID" > "$F.new" && chmod 600 "$F.new" && mv "$F.new" "$F" && jq -e --arg p "$PRIV" --arg h "$HOME" '.permissions.deny as $d | (.disableAllHooks == false) and (.sandbox.enabled == false) and (.permissions.disableBypassPermissionsMode == "disable") and (.permissions.allow == ["Bash(mp-run:*)", "Bash(mp-data:*)"]) and any(.hooks.PreToolUse[]; .matcher == "Bash" and any(.hooks[]; .command == $h + "/.local/bin/mp-bash-hook")) and all((["sources", "datasets", "workbook", "reference", "goldens", "corpus", "results", "logs", "assets"] | map("Read(/" + $p + "/" + . + "/**)"))[], "Read(/" + $p + "/scrub-map.json)", "Read(/" + $h + "/.config/modernization/data.env)", "Read(//proc/**)"; . as $r | any($d[]; . == $r))' "$F"
```
It must print `true`. A missing hook (`test -x` fails) means the `mp_data` item has not run: do 2(c) first. Never rely on `~/.claude/settings.json` alone: the repo's own settings can override it. `--settings` is the command-line scope and wins per key.

### 1.11 Consent and AI terms
Fill `data_policy.ai_terms.checked_on` and `data_policy.client_consent.signed_on` (and `document`) with the user. Prospects accept the one-page health-check terms before they send any file. Check:
```
. $HOME/engagements/<id>/run.env && jq -e '.data_policy | (.execution == "local") and (.redact == "always") and ((.client_consent.signed_on // "") | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$")) and ((.ai_terms.checked_on // "") | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$"))' "$REC/engagement.json"
```
It must print `true`. The driver refuses a client run otherwise.

### 1.12 Toolkit release for mp-data
For clients, `mp-data` runs a reviewed toolkit release, not the worktree: `$HOME/.local/share/modernization/toolkit@<sha>`, named in `$HOME/.local/share/modernization/toolkit.pin`, with its own `.venv` and the uv cache `$HOME/.local/share/modernization/uv-cache`. All three are read-only inside mp-run, so agent commands cannot change the code that reads client data. The pin is per host and serves every client engagement on it. Only you pin, in a plain terminal: `mp-data pin` refuses without a terminal, the client hook allows only `mp-data pin --status`, and agents list a pin in `manual_steps` when a toolkit change must touch client data.

mp-data must be installed before it can pin. On a new host, do the first command of 2(a) with `S=S0` and run 2(c) first: the `mp_data` item's fix installs mp-data from the pipeline checkout, and its check keeps failing until a pin exists, so the script stops there. `mp-data pin` works in the git checkout of the current directory and accepts only a sha that `origin/main` holds there, so the block changes into the pipeline clone. It first checks that main holds the S6 approval of both demos (the prerequisite above); it prints both dates, then mp-data's summary:
```
. $HOME/engagements/<id>/run.env && cd $HOME/sourcecode/modernization-pipeline && rgit . fetch origin && rgit . show origin/main:engagements/northwind-demo/gates.json | jq -e .S6.approved && rgit . show origin/main:engagements/invoicegen-demo/gates.json | jq -e .S6.approved && mp-data pin "$(rgit . rev-parse origin/main)"
```
pin checks the release out, syncs its `.venv`, fetches its jars and installs that release's mp-data, mp-run and mp-bash-hook. Then run 2(c) again: the `mp_data` check passes once `mp-data pin --status` names the pin and the installed copies equal the release's scripts. Pin again only when no client stage is running on this host: a new pin changes the toolkit of every client engagement here.

### 1.13 Start the client session
For each stage, first do 2(a), 2(c), 2(d) and 1.10 in a plain terminal. Then start the session from the pipeline worktree with the env file sourced, so `$HOME/.local/bin` is on PATH and `mp-data` and `mp-run` resolve as bare words:
```
. $HOME/.config/modernization/env && cd $HOME/engagements/<id>/pipeline && claude --settings $HOME/engagements/<id>/client-session.json --add-dir $HOME/engagements/<id>/target --add-dir $HOME/engagements/<id>
```
**First-session probe**, before the engagement's first S0 and on every new host. Every step must pass. On any failure, stop and do not run S0: fix the settings, the hook, the pin or the environment first.
1. In the session, run the Bash command `ls`. The hook must block it with a message that starts `client session: run shell commands as mp-run`.
2. In the session: `mp-run --eng <REC>/engagement.json --slot probe -- 'cat <PRIV>/sources/* | wc -c'` must print `0` (the client's files are there, but mp-run shows the directory empty).
3. In the session, a one-agent workflow must be blocked the same way (hooks cover workflow agents). Its answer must quote the hook message:
```
Workflow({ script: "export const meta = { name: 'hook-probe', description: 'One agent runs a plain ls in a client session' }\nreturn await agent('Run the Bash command ls once, exactly as written, and reply with its output or its error message.', { label: 'probe' })" })
```
4. In the session: `mp-data --slot probe fetch <REC>/engagement.json --verify --record-if-unset` must print a summary with status `ok` or `recorded`. A new source has no pins, and `--verify` alone exits 2 then; with `--record-if-unset` it writes `$REC/source.lock.json`, which S0 commits and you approve at the S0 gate.
5. In the plain terminal, the `postgres` environment check must pass. It proves that `mp_agent` (the login in PG_URL) is refused on database `mpdata_probe` and that `mp_runner` reaches `mpdata_probe` but is refused on `template1`. The `postgres` item belongs to S3 to S6, so on a new host its container may not exist yet and the first check fails: then run the fix as below. It reads the reviewed pipeline.json on `origin/main`, never the worktree's copy, which agents can edit:
```
. $HOME/engagements/<id>/run.env && bash -c "$(rgit "$PIPE" show origin/main:pipeline.json | jq -r '.environment[] | select(.id == "postgres") | .check')" && echo "postgres ok"
```
   If it fails, run the item's fix the same way (`.fix` instead of `.check`), then the check again.
6. In the plain terminal, run mp-run's own tests from the pinned release. They skip inside mp-run (a nested bwrap is refused under Ubuntu's AppArmor profile for bwrap), so on a client host this is where they run. Every test must pass and none may be skipped. The release, its `.venv` and its scripts are the reviewed copies that agents cannot write:
```
. $HOME/.config/modernization/env && cd $HOME/.local/share/modernization/toolkit@$(cat $HOME/.local/share/modernization/toolkit.pin) && UV_CACHE_DIR=$HOME/.local/share/modernization/uv-cache PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -q -rs -p no:cacheprovider toolkit/tests/test_mp_run.py
```
7. A link from the design zone into the data zone must not open. In the plain terminal: `mkdir -p <PRIV>/logs && printf 'symlink-canary\n' > <PRIV>/logs/probe.txt`. In the session: `mp-run --eng <REC>/engagement.json --slot probe -- 'mkdir -p <PRIV>/review && ln -sfn <PRIV>/logs/probe.txt <PRIV>/review/probe-link'`, then open `<PRIV>/review/probe-link` with the Read tool and search it for `canary` with the Grep tool. Both must be refused. If either returns the text `symlink-canary`, the deny rules do not cover links on this Claude Code version: stop. Then, in the plain terminal: `rm -f <PRIV>/review/probe-link <PRIV>/logs/probe.txt`.

---

## 2. Per stage

At a glance: (a) branches → (b) load → (c) environment → (d) paths.local.json → (e) items (S1, S5: from a first dry run) → (f) launcher and dry run → (g) prepare → (h) real run → logging (section 3) → (i) ready: PRs, gate, merge, or (j) not ready: fix and continue.

Clients: do (a), (c) and (d) in a plain terminal ((a) and (c) need the push token, Docker and data.env; mp-run in the session reads the paths.local.json that (d) writes), regenerate the settings (1.10), then start the session (1.13) and do (b) and the rest there, in the forms of 1.9.

### (a) Branches
Set the stage. `S` is the stage id (S0 to S6). This also fetches `origin/main`, which the `when` commands of the environment items read:
```
. $HOME/engagements/<id>/run.env && S=<stage id> && rgit "$PIPE" fetch origin --prune && SLUG=$(rgit "$PIPE" show origin/main:pipeline.json | jq -r --arg s "$S" '.stages[] | select(.id == $s) | .slug') && test -n "$SLUG" && sed -i '/^S=/d; /^SLUG=/d' $HOME/engagements/<id>/run.env && printf 'S=%s\nSLUG=%s\n' "$S" "$SLUG" >> $HOME/engagements/<id>/run.env
```
**The previous stage must be merged** (S1 and later; for a change order, S1 itself). `P` is the stage you ran last for this engagement. Do not stack branches.

Bootstrap order: northwind-demo runs S0 to S6 in order and merges S3 before its S4, because S4.models reuses `toolkit stage1 --schema-only` and `toolkit/typemap.py`, which S3 builds. invoicegen-demo starts after the northwind-demo S6 gate (section 6), so those tools exist by then; it skips S3 (2(f)).

Demos:
```
. $HOME/engagements/<id>/run.env && P=<previous stage id> && PS=$(rgit "$PIPE" show origin/main:pipeline.json | jq -r --arg s "$P" '.stages[] | select(.id == $s) | .slug') && gh pr list -R "$PIPE_GH" --head "eng/$ID/$P-$PS" --state merged --json number --jq length && gh pr list -R "$TREPO_GH" --head "$ID/$P-$PS" --state merged --json number --jq length
```
Clients with target.mode github (plain terminal; the target repo is queried through `mp-data gh`):
```
. $HOME/engagements/<id>/run.env && P=<previous stage id> && PS=$(rgit "$PIPE" show origin/main:pipeline.json | jq -r --arg s "$P" '.stages[] | select(.id == $s) | .slug') && gh pr list -R "$PIPE_GH" --head "eng/$ID/$P-$PS" --state merged --json number --jq length && mp-data gh "$REC/engagement.json" -- pr list --head "$ID/$P-$PS" --state merged --json number --jq length
```
Each number must be 1 or more. `mp-data gh` prints one JSON summary; gh's output lines (here the number) are in its `messages`. A 0 is fine only when that repo had no changes in that stage (`--state all` also gives 0). For target mode local, check instead that the local branch is gone (section 2(i) deletes it after the local merge): `rgit "$TREPO" branch --list "$ID/$P-$PS"` prints nothing.

**Check out the stage branches.** A branch that already exists on the remote holds a checkpoint from an earlier attempt; continue from it. Otherwise start from `origin/main`. A repo that is already on the stage branch is left as it is, so the dirty-tree check applies only when switching to the stage branch. The parentheses keep `exit` from closing a plain terminal:
```
( . $HOME/engagements/<id>/run.env
for spec in "$PIPE|eng/$ID/$S-$SLUG" "$TREPO|$ID/$S-$SLUG"; do
  dir=${spec%%|*}; br=${spec#*|}
  if [ "$(rgit "$dir" branch --show-current)" = "$br" ]; then echo "$dir: already on $br (continuing); uncommitted work of not-done tasks kept"; rgit "$dir" status --short; continue; fi
  test -z "$(rgit "$dir" status --porcelain)" || { echo "dirty tree in $dir"; exit 1; }
  if rgit "$dir" remote get-url origin >/dev/null 2>&1; then
    rgit "$dir" fetch origin --prune || exit 1
    if rgit "$dir" ls-remote --exit-code --heads origin "$br" >/dev/null; then
      rgit "$dir" checkout -B "$br" "origin/$br" || exit 1; echo "$dir: continuing $br from its checkpoint"
    else
      rgit "$dir" checkout --no-track -B "$br" origin/main || exit 1; echo "$dir: new $br from origin/main"
    fi
  else
    rgit "$dir" checkout "$br" 2>/dev/null || rgit "$dir" checkout -b "$br" main || exit 1; echo "$dir: local $br"
  fi
done
test "$REC" = "$PIPE/engagements/$ID" || test -z "$(rgit "$REC" status --porcelain)" || { echo "dirty records repo"; exit 1; } )
```

### (b) Load
Read these files from the checked-out branches (Read tool), never from memory of an earlier stage:

| File | Use |
|---|---|
| `$PIPE/pipeline.json` | embedded by the launcher (2(f)), sliced to this stage; read it to check it, never paste it into args |
| `$REC/engagement.json` | embedded by the launcher (2(f)); read it to check it, never paste it into args |
| `$REC/source.lock.json` | written at S0 when the engagement has no pins; must exist from S1 on for such engagements. The toolkit reads it; you only check it is there and was approved at the S0 gate |
| `$REC/inventory.lock.json` | recorded counts when `expected_inventory` has no numbers; same rule |
| `$REC/gates.json` | embedded by the launcher (2(f)) when it exists; read it to check it, never paste it into args; demos have one too (section 4) |
| `$REC/runs/$S.json` (a change-order run: `$REC/runs/S1-<CO-NNN>.json`) | exists when this stage ran before; the launcher embeds its `results` and `items` as `prior` and `prior_items` when generated with `--prior`: see (j) |

Once S0 has merged, lint the engagement:
```
. $HOME/engagements/<id>/run.env && . "$ENVF" && cd "$PIPE" && uv run toolkit engagement-lint "$REC/engagement.json"
```
In a client session run it as `mp-run --eng <REC>/engagement.json --slot orchestrator -- 'uv run toolkit engagement-lint <REC>/engagement.json'`. Clients: also run the checks in 1.8 and 1.11.

### (c) Environment
`pipeline.json` has one `environment` list; stages have no preflight list of their own. An item applies when its `stages` include the stage, its `kinds` (if any) include `source.kind`, and its `when` command (if any) exits 0. For each applicable item, in list order: run `check`; if it fails, run `fix`, then `check` again; stop on a failure unless the item is `optional`. Items without a `fix` (docker, gh, bwrap, workflow_size) stop and ask the user. `env_file` comes first and creates the env file and data.env (both chmod 600; secrets only in data.env); `uv` and `jq` come next, because later items use them. The order is `env_file`, `uv`, `jq`, `python313`, `docker`, `gh`, `bwrap`, `jdk`, `syslibs`, `postgres`, `mssql`, `mp_data`, `hooks`, `chromium`, `libreoffice`, `workflow_size`. `jq`, `jdk` and `bwrap` apply to every stage and engagement; `mssql` applies only when its `when` finds mssql in `database.primary` or `database.verify_also`.

These commands are never wrapped in mp-run or anchored: they need Docker and data.env, which mp-run hides. The dry run returns the same items, filled, in `environment`. Demos: run the script below yourself, in the session. Clients: the user runs it in a plain terminal before the session starts (1.13).

Generate the script for this stage. It takes the list from the reviewed pipeline.json on `origin/main` (as the `when` commands do), never from the checked-out copy, which agents can edit:
```
. $HOME/engagements/<id>/run.env && KIND=$(jq -r .source.kind "$REC/engagement.json") && PJ=$(rgit "$PIPE" show origin/main:pipeline.json) && { cat <<'EOF'
#!/bin/bash
# Environment preflight generated by docs/runbook.md section 2(c). Stops at the first required item that fails.
item() {
  id=$1 optional=$2 when=$3 check=$4 fix=$5
  if ! bash -c "$when" >/dev/null 2>&1; then echo "skip $id (when is false)"; return 0; fi
  if bash -c "$check"; then echo "ok $id"; return 0; fi
  if [ -n "$fix" ]; then echo "fixing $id"; bash -c "$fix" && bash -c "$check" && { echo "ok $id (fixed)"; return 0; }; fi
  if [ "$optional" = true ]; then echo "missing $id (optional)"; return 0; fi
  echo "FAILED $id"; exit 1
}
EOF
printf '%s\n' "$PJ" | jq -r --arg S "$S" --arg kind "$KIND" --arg pipeline "$PIPE" --arg target_repo "$TREPO" --arg target "$TARGET" --arg private "$PRIV" --arg records "$REC" --arg env_file "$ENVF" 'def fill: reduce ($ARGS.named | to_entries[]) as $kv (.; gsub("\\{\\{p\\." + $kv.key + "\\}\\}"; $kv.value)); .environment[] | select(.stages | index($ARGS.named.S)) | select(.kinds == null or (.kinds | index($ARGS.named.kind))) | "item \(.id | @sh) \(.optional // false) \((.when // "true") | fill | @sh) \(.check | fill | @sh) \((.fix // "") | fill | @sh)"'; } > $HOME/engagements/<id>/preflight-$S.sh
```
Run it (background the first time on a machine: the JDK, which every stage and source kind needs, is a 207 MB download):
```
bash $HOME/engagements/<id>/preflight-<stage id>.sh
```
Show the user the `ok`, `skip`, `fixing` and `FAILED` lines. The `uv` and `python313` lines record what was provisioned.

The `mp_data` check only tests that mp-data, mp-run and mp-bash-hook are installed as mode-755 copies (for clients, also that they equal the pinned release's scripts). Demos: after a merge that changed `scripts/mp-data`, `scripts/mp-run` or `scripts/mp-bash-hook`, run the item's fix once after (a) has checked out the new stage branch from `origin/main`: `. $HOME/engagements/<id>/run.env && "$PIPE/scripts/install-mp-data.sh"`. Clients get changed scripts only through a new pin (1.12).

### (d) paths.local.json
```
. $HOME/engagements/<id>/run.env && jq -n --arg pipeline "$PIPE" --arg target_repo "$TREPO" --arg target "$TARGET" --arg private "$PRIV" --arg records "$REC" --arg env_file "$ENVF" '{format_version: 1, pipeline: $pipeline, target_repo: $target_repo, target: $target, private: $private, records: $records, env_file: $env_file}' > "$REC/paths.local.json"
```
It is gitignored. The toolkit, mp-data and mp-run read it next to engagement.json, so write it before any of them runs; `args.paths` uses the same six paths.

### (e) Fan-out items
S1 (`S1.areas`) and S5 (`S5.features`) fan out over areas. For each fan-out task, the dry run (f) returns `items_from.<task id>.cmd`: the task's `items_from.jq` program from pipeline.json, run on its `items_from.file` (`<TARGET>/analysis/rules.json`), as an mp-run line with the stage's prepare slot, for example `mp-run --eng <REC>/engagement.json --slot s1_prepare -- 'jq -c '\''<program>'\'' <TARGET>/analysis/rules.json'`. So:
1. Do the dry run (f) without `items`. Missing items are no problem in a dry run; only the real run needs them.
2. Run `items_from.<task id>.cmd` exactly as returned, as its own Bash call (a plain jq call is blocked in client sessions). It prints one JSON array.
3. Put `items: {"<task id>": <that array>}` in `args-<S>.json` (2(f)), regenerate the launcher and do a second dry run; keep the items for the real run.

- **S1:** on the first run `rules.json` does not exist yet and the command fails: pass no items, and the scout picks the areas. On every later S1 run, pass the items, so the split stays stable: the `S1.areas` program lists every area, kept and dropped, with its objects as `type: name`. If you continue before rules.json exists, pass no items: the launcher generated with `--prior` carries `prior_items` (see (j)).
- **S5:** the `S5.features` program lists the kept areas of the signed rules (docs/contracts.md section 8).

More items than `max_items` (4) block the task unless the user agrees to `allow_over_budget: true` (in `args-<S>.json`).

### (f) Launcher and dry run
A Workflow script cannot read files, and the Workflow tool refuses a script over 524288 bytes, so pipeline.json (about 500 KB as compact JSON), engagement.json, gates.json, `prior` and `prior_items` never go into a Workflow call. Never paste them into `args`. A per-stage launcher embeds them instead: `.claude/workflows/make-launcher.py` (standard library only) writes `$HOME/engagements/<id>/launch-<S>.workflow.js`, which holds pipeline.json sliced (the top-level keys, this stage in full, every other stage reduced to `id`, `slug`, `title`, `applies_to`, `offers`, `offer_note` and `bootstrap_only`; the dry run is the same as with the whole file), engagement.json, gates.json when it exists, and your args file. With `--prior` it also embeds the `results` and `items` of `$REC/runs/<S>.json` as `prior` and `prior_items` (a change-order run: `$REC/runs/S1-<CO-NNN>.json`, which it picks when the args name `change_order`). The launcher calls run-stage.js with `workflow({ scriptPath })`. make-launcher.py exits 2 on a missing or invalid file, on a file key in the args file, and on a launcher over 500000 bytes.

1. Write the other driver args with the Write tool to `$HOME/engagements/<id>/args-<S>.json`, a JSON object with the absolute values from run.env:
```
{
  "paths": { "pipeline": "<PIPE>", "target_repo": "<TREPO>", "target": "<TARGET>", "private": "<PRIV>", "records": "<REC>", "env_file": "<ENVF>" },
  "items": <from (e), when there are any>,
  "host_attested": <true for clients after 1.8; omit for demos>,
  "commit_trailer": "<the commit attribution lines this session was given, joined with \n; empty if none>",
  "attempt": 1,
  "dry_run": true
}
```
   It never holds `pipeline`, `engagement`, `gates`, `prior` or `prior_items`: the launcher adds them from disk. `stage_id` comes from `--stage`.
2. Generate the launcher. Demos, in the session:
```
. $HOME/engagements/<id>/run.env && python3 -I "$PIPE/.claude/workflows/make-launcher.py" --pipeline "$PIPE" --records "$REC" --stage "$S" --args "$HOME/engagements/$ID/args-$S.json" --out "$HOME/engagements/$ID/launch-$S.workflow.js"
```
   Clients, in the session, as one mp-run call with the stage's prepare slot (`s2_prepare` for S2) and `<PIPE>`, `<REC>`, `<S>` and `<id>` typed out (`$HOME` inside the single quotes is fine: mp-run's shell expands it, and the hook allows `$` there):
```
mp-run --eng <REC>/engagement.json --slot <s>_prepare -- 'python3 -I <PIPE>/.claude/workflows/make-launcher.py --pipeline <PIPE> --records <REC> --stage <S> --args $HOME/engagements/<id>/args-<S>.json --out $HOME/engagements/<id>/launch-<S>.workflow.js'
```
   Add `--prior` when continuing (2(j)). It prints one JSON line with the launcher path and its size. Regenerate the launcher after every change to `args-<S>.json` and to what it embeds: pipeline.json or engagement.json on the stage branches, gates.json, a new runs file. A launcher holds copies, so an old one runs with old inputs.
3. Run the dry run through it. The call has no `args`; the launcher carries them:
```
Workflow({ scriptPath: "<the launcher path that step 2 printed>" })
```
Then check:
- `problems` is empty. Otherwise stop and show them (troubleshooting, section 8).
- `branches` are the ones you checked out in (a).
- Every id in `preflight` passed or was skipped in (c). The driver derives `preflight` from the environment list in its order; `environment` holds the same items with filled commands, unwrapped.
- `prepare` and `exit_criteria` come in the command forms below.
- `estimate.worst` is not above the workflow size set in 0.7.

If the dry run returns `not_applicable: true` (S3 for an xlsm source), skip the stage: no prepare, no real run, no PRs, no gates.json entry or log row. Run `git -C <repo> checkout main` and `git -C <repo> branch -D <stage branch>` for the local branches (a) created. For the next stage's (a), `P` is the last stage that applied.

**Command forms.** The driver anchors every command it returns or gives to an agent (docs/contracts.md section 1):
- A data-plane call stays one standalone `mp-data` call with the slot added and any `$MP_DB_SUFFIX` replaced by it:
  `mp-data --slot s1_prepare fetch <REC>/engagement.json --verify`
- Every other command, from S1 on and in every client S0, runs through mp-run (single quotes inside the command are written `'\''`):
  `mp-run --eng <REC>/engagement.json --slot s1_prepare -- 'uv run toolkit inventory <REC>/engagement.json --model <PRIV>/out/model --out <PRIV>/out/inventory.json --expect'`
- Only S0 in bootstrap mode (mp-run does not exist yet) uses the braced form:
  `. <ENVF> && export MP_DB_SUFFIX=s0_workspace && cd <PIPE> && { uv run pytest -q; }`
- Environment commands have no form: they run as written (section 2(c)).
- Integrate and checkpoint run their own git and `date` commands in the same forms with their own slot (`s1_integrate`, `s1_checkpoint`): through mp-run from S1 on and in every client S0, directly only in S0 bootstrap. In every demo stage their checks include `mp-data --slot <slot> guard <REC>/engagement.json --repo <PIPE> --repo <TREPO> --history`.
- Every agent command (acceptance, verify, exit, prepare) is one line, because the client hook blocks newlines; environment commands are exempt.

The slot is the agent label (`<stage>:prepare` for the prepare list and `items_from`, `<stage>:integrate` for the exit criteria) in lower case, with every run of characters outside `[a-z0-9]` replaced by `_` and leading and trailing `_` dropped: `s1_prepare`, `s1_integrate`, `s5_feature_orders`. A slot longer than 24 characters becomes its first 17 characters, `_` and a 6-character hash (docs/contracts.md section 1).

Show the user the task table (id, kind, depends_on, applies), `tasks_not_applicable`, `items_from`, the estimate and the gate name. Continue unless the user asked to confirm first.

### (g) Prepare
Each stage has a `prepare` list that rebuilds the data zone (fetch, parse, inventory, dataset export, reference build, private goldens). The S0 (clients only), S1 and S2 lists also run `uv run toolkit fetch --jars`, which fills the pipeline worktree's gitignored `vendor/` with the reference jars that the toolkit tests need (a client's mp-data fetches jars only into the pinned release). The S5 and S6 lists start with `cd <TARGET>/app && uv sync --locked`, because mp-data's Django cells run the app's own `.venv/bin/python` (there is no uv inside its jail). It is idempotent. Take it from the dry run and run each line exactly as returned, in order, each as its own Bash call: background calls for fetch, reference build and golden record, which can take longer than 10 minutes. Do not re-anchor a line, prefix `. <env file>` or join lines with `&&`: data-plane lines must stay standalone, and in a client session the hook allows nothing else. Stop at the first line that exits non-zero (one exception: the bootstrap S0 of a new source kind, below).

`mp-data` prints one JSON summary per call; show the user each `status`. If anything fails, stop: it is an environment or source problem, not a task for agents. A changed source (`fetch --verify` or `inventory --expect` mismatch) is never fixed by updating pins: ask the user.

**S0.** Its prepare lines are `mp-data --slot s0_prepare --if-built fetch|parse|dataset export ...` and the jar fetch above, which in S0 runs only for clients (`jq -e '.bootstrap == true' <REC>/engagement.json >/dev/null || uv run toolkit fetch --jars`: a demo's mp-data fetch fills the worktree's `vendor/` itself, and a demo's first S0 has no `toolkit fetch` yet). In bootstrap mode an mp-data command that is not built yet answers with status `not-built` and exits 0; any other failure stops the list, as in every stage. Only in the very first S0 bootstrap run is mp-data itself missing. Check:
```
. $HOME/.config/modernization/env && command -v mp-data
```
When it fails (prints nothing), skip the S0 prepare list. That is the only case in which a whole prepare list is skipped. Clients never skip it: mp-data is installed and pinned before their first S0 (1.12), and their S0 prepare `fetch` line records the source lock when there is none (`--record-if-unset`).

**Bootstrap S0 of the first engagement of a source kind** (invoicegen-demo is the first xlsm): fetch, parse and dataset export for that kind were built earlier and tested on fixtures only. If `parse` or `dataset export` exits non-zero with status `failed` or `error`, do not stop (a `fetch --verify` pin or hash mismatch still stops): show the summary, skip the later prepare lines and run (h) with `prepared: "S0"`. S0.parse and S0.dataset fix the reader in this run; their acceptance and the S0 exit criteria run the same commands.

### (h) Real run
The same call through the same launcher, regenerated from `args-<S>.json` without `dry_run`, plus:
- `prepared: "<S>"`, only after every prepare line passed (or, in the very first S0, after you skipped the list because mp-data is not installed yet; or, in the bootstrap case of (g), after a parse or dataset export failure);
- `max_agents: <estimate.worst from the dry run>`;
- when continuing: the launcher regenerated with `--prior` (it carries `prior` and `prior_items`), plus `rerun`, `since` and the next `attempt` (see (j));
- change orders: `change_order: "CO-NNN"` (section 5).

Write the args file with the Write tool and regenerate the launcher (2(f) steps 1 and 2), then make the same `Workflow({ scriptPath })` call. The result has a `runId` (for example `wf_7344f6f1-5af`). Note it, then do section 3 (logging) whether the run is ready or not.

The integrate agent runs the exit criteria. If you run one yourself (for example to show the user a failure), use the dry run's `exit_criteria` string exactly, as its own Bash call.

### (i) Ready
1. Check `ready: true`, every `integrate.exit_criteria` entry passed, and `integrate.repos` shows each repo committed on the stage branch (and pushed, except a local-mode target). Integrate pushes a client target repo with `mp-data push`.
2. Write the PR body to `$HOME/engagements/<id>/pr-<S>.md`: one line per task (status, summary), minor issues, `manual_steps`, requests applied, the agent count and the gate name. No data values: the demo repo's PRs become public at the northwind-demo S6 gate.
3. Open the PRs (ask first). Demos:
```
. $HOME/engagements/<id>/run.env && T=$(jq -r --arg s "$S" '.stages[] | select(.id == $s) | .commit_message' "$PIPE/pipeline.json" | sed "s/{{s.id}}/$S/g; s/{{e.id}}/$ID/g") && gh pr create -R "$PIPE_GH" --base main --head "eng/$ID/$S-$SLUG" --title "$T" --body-file $HOME/engagements/<id>/pr-$S.md && gh pr create -R "$TREPO_GH" --base main --head "$ID/$S-$SLUG" --title "$T" --body-file $HOME/engagements/<id>/pr-$S.md
```
   Clients: open the pipeline PR with the first `gh pr create` above, through mp-run (1.9). Open the target PR (target.mode github) as one data-plane call, with the title typed out:
```
mp-data gh <REC>/engagement.json -- pr create --base main --head <id>/<S>-<slug> --title '<title>' --body-file <absolute path of pr-S.md>
```
   `mp-data gh` prints one JSON summary; gh's output (here the PR URL) is in its `messages`, and so are the URL and SHA from the `pr view` in step 5. Skip a repo whose stage branch has no commits beyond main. After the gate, delete such a pipeline branch: `git -C "$PIPE" push origin --delete "eng/$ID/$S-$SLUG"` (clients: through mp-run); otherwise a later 2(a) for that stage (a change order) would continue from it as a checkpoint. Target mode local: no target PR; show `git -C "$TREPO" diff --stat main..."$ID/$S-$SLUG"` instead.

4. Wait for checks with **background** Bash calls, one per repo. Demos:
```
. $HOME/engagements/<id>/run.env && gh pr checks "eng/$ID/$S-$SLUG" -R "$PIPE_GH" --watch --fail-fast --interval 30
. $HOME/engagements/<id>/run.env && gh pr checks "$ID/$S-$SLUG" -R "$TREPO_GH" --watch --fail-fast --interval 30
```
   Clients: the first line through mp-run; the target repo as `mp-data gh <REC>/engagement.json -- pr checks <id>/<S>-<slug> --watch --fail-fast --interval 30`.
   From S0 on the target repo runs guard.yml on every PR. If gh reports no checks, run it again after a minute, then `gh run list -R <repo> --branch <branch>`; a missing run is a failure. Any other non-zero exit is a CI failure: show `gh run list -R <repo> --branch <branch>` and `gh run view <run id> -R <repo> --log-failed` (client target: `mp-data gh <REC>/engagement.json -- run list --branch <branch>` and `-- run view <run id> --log-failed`), and stop.

5. Show the gate. Get the head commits the user is approving. Demos:
```
. $HOME/engagements/<id>/run.env && gh pr view "eng/$ID/$S-$SLUG" -R "$PIPE_GH" --json url,headRefOid --jq '"\(.url) \(.headRefOid)"' && gh pr view "$ID/$S-$SLUG" -R "$TREPO_GH" --json url,headRefOid --jq '"\(.url) \(.headRefOid)"'
```
   Clients: the first `gh pr view` through mp-run; the target as `mp-data gh <REC>/engagement.json -- pr view <id>/<S>-<slug> --json url,headRefOid --jq '"\(.url) \(.headRefOid)"'`.
   Target mode local: `git -C "$TREPO" rev-parse "$ID/$S-$SLUG"` (clients: through mp-run, before the local merge deletes the branch) is the target head SHA; use it as `TSHA` in section 4 and in section 5 step 3.
   Show `gate.name`, `gate.kind`, `gate.checklist` and `gate.est_minutes` from the result, the PR URLs, the check results and the two head SHAs. Ask the user to work through the checklist and to tell you: approved or not, and minutes spent on the gate, on PR review and on anything else. Checklist steps that touch Docker, data.env, the data zone or `mpdata_` databases are the user's, in a plain terminal (rules).

6. On approval: fill the three human-minute columns of this stage's log row (section 3) and update `gates.json` (section 4). If a gate step changed a file on a stage branch, commit and push that first and take hashes at the new head (client target: push it with `mp-data push <REC>/engagement.json --branch <id>/<S>-<slug>`; public demo repo: run the guard first, rules).
   - Demos (records in the pipeline repo):
```
. $HOME/engagements/<id>/run.env && git -C "$PIPE" add "engagements/$ID/log.md" "engagements/$ID/gates.json" && git -C "$PIPE" commit -m "$S $ID: gate approved, minutes logged" -m "<attribution lines>" && git -C "$PIPE" push origin "eng/$ID/$S-$SLUG"
```
   Then wait for the pipeline checks again (step 4).
   - Clients (records repo, no push; through mp-run):
```
. $HOME/engagements/<id>/run.env && git -C "$REC" add log.md gates.json && git -C "$REC" commit -m "$S $ID: gate approved, minutes logged"
```
7. Squash-merge both PRs (ask first). `--match-head-commit` refuses the merge if anything was pushed after what the user approved. Demos:
```
. $HOME/engagements/<id>/run.env && gh pr merge "eng/$ID/$S-$SLUG" -R "$PIPE_GH" --squash --delete-branch --match-head-commit "$(git -C "$PIPE" rev-parse HEAD)"
. $HOME/engagements/<id>/run.env && gh pr merge "$ID/$S-$SLUG" -R "$TREPO_GH" --squash --delete-branch --match-head-commit <target head SHA shown at the gate, or after your gate commit>
```
   Clients: the first line through mp-run; the target as `mp-data gh <REC>/engagement.json -- pr merge <id>/<S>-<slug> --squash --delete-branch --match-head-commit <target head SHA>`.
   Target mode local:
```
. $HOME/engagements/<id>/run.env && git -C "$TREPO" checkout main && git -C "$TREPO" merge --squash "$ID/$S-$SLUG" && git -C "$TREPO" commit -m "<stage commit message>" -m "<attribution lines>" && git -C "$TREPO" branch -D "$ID/$S-$SLUG"
```
8. Run the budget check (section 3). Invoices and other commercial steps are in the gate checklist. For the S6 gate of a demo, continue with section 6.

### (j) Not ready
1. Show the user, from the report: `blockers`, `blocking`, the minor `issues`, `requests`, `manual_steps`, every task that is not done (with its summary), `exit_failed`, `exit_missing` and `checkpoint`. The driver has already checkpointed: done tasks' owned paths are committed as `WIP <S> <id>: attempt N not ready` on both stage branches and pushed, and `$REC/runs/<S>.json` (a change-order run: `$REC/runs/S1-<CO-NNN>.json`) plus a log row are written. If `checkpoint` is missing or failed, save the report yourself to that file (without `integrate` and `checkpoint`) and commit it like a gate record (step (i) 6).
2. Fix the cause, then commit your fix so the trees are clean:
   - **Environment:** the blocker names the item; rerun (c) (clients: in a plain terminal).
   - **Client answers:** write each under its `## Q-<area>-NNN` heading in `$REC/answers.md`, with `answered: YYYY-MM-DD` and `by: client` (or `by: you` for demos). Rules, never data values. Demos: commit on the pipeline stage branch and push. Clients: commit in the records repo.
   - **Dependency request** (`dependency: <spec> for <reason>`): add it on the stage branch, then commit and push.
```
. $HOME/engagements/<id>/run.env && . "$ENVF" && cd "$PIPE" && uv add '<spec>' && git -C "$PIPE" add pyproject.toml uv.lock && git -C "$PIPE" commit -m "$S $ID: add <spec>" -m "<attribution lines>" && git -C "$PIPE" push origin "eng/$ID/$S-$SLUG"
. $HOME/engagements/<id>/run.env && . "$ENVF" && cd "$TARGET/app" && uv add '<spec>' && git -C "$TREPO" add "${TPFX}app/pyproject.toml" "${TPFX}app/uv.lock" && git -C "$TREPO" commit -m "$S $ID: add <spec>" -m "<attribution lines>" && git -C "$TREPO" push origin "$ID/$S-$SLUG"
```
   The first form is for toolkit packages (add `--group excel` or `--group dev` when the request names the group), the second for app packages (S4 and later). App packages requested in S5 or S6 are fine: `new-app --check` checks only that the `stack.app_packages` pins are present in `app/pyproject.toml` and `uv.lock`, so it stays green. Clients: run both through mp-run, the second without its final `git push`; then push the target branch with `mp-data push <REC>/engagement.json --branch <id>/<S>-<slug>`. Public demo repo: run the guard before the target push (rules).
   - **Manual steps:** assign them to the gate or do them; a step with `blocks_gate: true` must be done before approval.
   - **Exit check failed, every task done** (blocker `<n> exit check(s) failed`; `exit_failed` lists the lines): run each failing line yourself exactly as the dry run returned it, as its own Bash call, and read its summary. Put in `rerun` the task whose acceptance or verify holds the same command, or that owns the paths the failure names: S2 `golden status` → `S2.golden`; S5 whole-app pytest, the rule-id and e2e-id greps, or `equivalence` → the `S5.feature.<area>` instances named by the failing test ids, rule ids or the summary `units` (their areas), plus `S5.shared` when `domain/` fails; S6 `report --check` or results → `S6.report`. Put the failing line and its last error line in `since`. If no task matches, it is a pipeline change: fix it on the stage branch and rerun the task that uses it.
   - **S0 not ready with S0.workspace not done (clients):** the prepare fetch recorded `$REC/source.lock.json`, which only S0.workspace commits, so 2(a) would stop with "dirty records repo". Commit it yourself in the plain terminal: `. $HOME/engagements/<id>/run.env && rgit "$REC" add source.lock.json && rgit "$REC" commit -m "$ID: source lock recorded (approve at the S0 gate)"`; its approval stays at the S0 gate.
   - **S0 custom quote:** when the health check names a custom quote (more than one data back end, a compiled-only file, a link parse cannot classify or an encrypted source), the parse exit criterion with `--fail-on` fails by design, so S0 is not ready and no fix or rerun changes that. Send the health check with the custom quote as the S0 client gate says, offer no fixed-price stage, log your minutes and stop the engagement there.
3. Continue. `rerun` lists the task or instance ids to redo: the blocked ones and any whose input you changed (for example `S1.synthesize` and the affected `S1.area.<id>`). `since` is one line on what changed ("answers.md: Q-orders-002 answered"). `attempt` is the report's `attempt` plus 1.
   - **Same session:** put the new `attempt`, `rerun` and `since` in `args-<S>.json` and regenerate the launcher with `--prior` (2(f)); it embeds `prior` and `prior_items` from `$REC/runs/$S.json` (a change-order run: `$REC/runs/S1-<CO-NNN>.json`), which the checkpoint wrote (or you saved in step 1). Run the real run (h) through it. Do not rely on `resumeFromRunId`: every prompt carries the attempt number, so a raised attempt changes every prompt and a resume returns nothing from its cache; `prior` reuses the done tasks without an agent.
   - **New session** (resume ids do not cross sessions): do (a) to (d) (the stage branches are taken from the checkpoint), start the session (0.8 or 1.13), and take the attempt from the previous report (a change-order run: `"$REC/runs/S1-<CO-NNN>.json"`; clients: through mp-run, 1.9):
```
. $HOME/engagements/<id>/run.env && jq '.attempt + 1' "$REC/runs/$S.json"
```
   Then do (e) if it applies, the dry run (f), the prepare list (g) and the real run (h) with that attempt, `rerun` and `since` in `args-<S>.json`, and generate each launcher with `--prior`. Tasks done in the prior run whose inputs did not change are reused without an agent.
   - **S2 in a new session with reused tasks:** the S2 prepare list cannot rebuild the reference databases and private goldens (S2 builds those tools). After (g), run line 1 and line 2 below only when `S2.reference` is reused (done in prior and not in `rerun`), and line 3 only when `S2.golden` is reused too; skip a line whose task runs again, because its acceptance rebuilds the same state (`S2.golden` acceptance [2] to [4], `S2.harness` acceptance [4] to [6]). Run each as its own Bash call (background), before the real run. The first builds the seed databases in the design zone (and the catalog when it is missing or stale) through mp-run; the other two are data-plane calls. `reference build --dataset all` exits 2 and names the first two lines, because mp-data's jail lets a data-plane call write only the data zone and its declared outputs. The S5 and S6 prepare lists rebuild the same databases with the same lines.
```
mp-run --eng <REC>/engagement.json --slot s2_prepare -- 'uv run toolkit reference build <REC>/engagement.json --dataset seeds'
mp-data --slot s2_prepare reference build <REC>/engagement.json --dataset engagement
mp-data --slot s2_prepare golden record <REC>/engagement.json --dataset engagement --private-only --check
```
   - **S3, S4 or S6 in a new session with reused tasks, after a database container was recreated (`docker rm -f mp-mssql22`, section 8) or with a new private directory:** add `S3.move` (the Stage 1 load in `mpdata_<db_name>_stage1`, which `S3.review` and the exit criteria verify), `S4.models` (`<db_name>_dev`, which its verify reads) or `S6.report` (the JUnit files in `private/results/S6` and `private/synthetic/results/S6` that `S6.audit`'s verify summarizes) to `rerun`, because the prepare lists do not rebuild them.

---

## 3. Logging

`$REC/log.md` has one row per run (docs/contracts.md section 15):

`| date | stage | attempt | run_id | mode | agents | est typical/worst | fix rounds | input tok | cache-write tok | cache-read tok | output tok | est cost USD | human gate min | human review min | human other min | ready | blockers |`

- The integrate or checkpoint agent appends the row: date, stage, attempt, `pending` for run_id, mode, agents spawned (itself included), estimate, fix rounds, `see runbook` in the four token columns and the cost column, `pending` in the three human columns, ready, and the not-done task ids.
- You fill `run_id`, the four token columns and `est cost USD` right after the run returns, ready or not. You fill the three human-minute columns at the gate (section 2(i) step 6). Put setup time (section 0) in the S0 row of northwind-demo as "human other min".
- Edit the row in place and commit it before anything else runs: demos on the pipeline stage branch (`git -C "$PIPE" add "engagements/$ID/log.md"`, commit, push), clients in the records repo. At a gate, commit it together with the gate record (section 2(i) step 6).

**Tokens and cost of one run.** Usage lines repeat per message id, so keep the last line of each message. This counts the workflow's agents only, not this session. In a client session, run it as a file through mp-run (1.9).
```
RUN_ID=<runId> && WF=$(ls -d $HOME/.claude/projects/*/*/subagents/workflows/$RUN_ID) && ls "$WF"/agent-*.meta.json | wc -l && PRICES='{"claude-opus-5-5":{"in":4,"cw5":5,"cw1h":8,"cr":0.2,"out":20},"claude-sonnet-5-5":{"in":2,"cw5":2.5,"cw1h":4,"cr":0.2,"out":10}}' && cat "$WF"/agent-*.jsonl | jq -s --argjson p "$PRICES" '[.[] | select(.message.usage and .message.id)] | group_by(.message.id) | map(last.message) | map(.usage as $u | {model, in: ($u.input_tokens // 0), cw5: ($u.cache_creation.ephemeral_5m_input_tokens // $u.cache_creation_input_tokens // 0), cw1h: ($u.cache_creation.ephemeral_1h_input_tokens // 0), cr: ($u.cache_read_input_tokens // 0), out: ($u.output_tokens // 0)}) | {input: (map(.in) | add), cache_write: (map(.cw5 + .cw1h) | add), cache_read: (map(.cr) | add), output: (map(.out) | add), unpriced: ([.[] | select($p[.model] == null) | .model] | unique), cost_usd: ((map(. as $m | $p[$m.model] as $r | if $r then ($m.in * $r.in + $m.cw5 * $r.cw5 + $m.cw1h * $r.cw1h + $m.cr * $r.cr + $m.out * $r.out) / 1e6 else 0 end) | add) * 100 | round / 100)}'
```
The directory is named exactly like the runId. The file count is a cross-check of `agents.spawned`.

**Prices** (USD per million tokens, Anthropic API list prices as published on 2026-10-06; check the pricing page before each engagement and update `PRICES`):

| Model | Input | Cache write 5 min | Cache write 1 h | Cache read | Output |
|---|---|---|---|---|---|
| claude-opus-5-5 | 4.00 | 5.00 | 8.00 | 0.20 | 20.00 |
| claude-sonnet-5-5 | 2.00 | 2.50 | 4.00 | 0.20 | 10.00 |

If `unpriced` is not empty, add that model's prices and run it again. On a subscription the dollar figure is a notional API equivalent; it still feeds the business plan's AI-cost line (100 to 400 USD per project).

**Budget.** After every gate, sum the human minutes and compare with `commercial.budget_hours` (40 for northwind-demo, 20 for invoicegen-demo; clients: set at the S1 gate):
```
. $HOME/engagements/<id>/run.env && B=$(jq -r '.commercial.budget_hours // empty' "$REC/engagement.json") && if [ -z "$B" ]; then echo 'no commercial.budget_hours yet (set at the S1 gate)'; else awk -F'|' -v b="$B" '$3 ~ /^ *S[0-6] *$/ { for (i = 15; i <= 17; i++) if ($i ~ /^ *[0-9]+ *$/) m += $i; if ($14 ~ /^ *[0-9.]+ *$/) c += $14 } END { p = 100 * m / (b * 60); printf "human %d of %d min (%.0f%%), AI cost %.2f USD\n", m, b * 60, p, c; exit (p >= 100 ? 2 : (p >= 75 ? 1 : 0)) }' "$REC/log.md"; fi
```
Exit 1 (75% or more): warn the user. Exit 2 (100% or more): stop before the next stage and ask. With no budget yet it prints a note and exits 0.

**Recalibration and rate** (after the last gate of an engagement). This prints the total human hours from log.md (the same columns as the budget check), `hours.point` of `$REC/estimate.json`, `commercial.budget_hours`, `commercial.quote`, the ratios of actual to estimated and to budgeted hours, and the effective rate (quote / actual hours):
```
. $HOME/engagements/<id>/run.env && H=$(awk -F'|' '$3 ~ /^ *S[0-6] *$/ { for (i = 15; i <= 17; i++) if ($i ~ /^ *[0-9]+ *$/) m += $i } END { printf "%.2f", m / 60 }' "$REC/log.md") && jq -n --argjson h "$H" --slurpfile e "$REC/estimate.json" --slurpfile g "$REC/engagement.json" '$e[0].hours.point as $p | $g[0].commercial.budget_hours as $b | $g[0].commercial.quote as $q | {actual_hours: $h, estimated_hours: $p, budget_hours: $b, quote_usd: $q, actual_over_estimate: (if ($p // 0) > 0 then ($h / $p * 100 | round / 100) else null end), actual_over_budget: (if ($b // 0) > 0 then ($h / $b * 100 | round / 100) else null end), effective_rate_usd_per_hour: (if $q != null and $h > 0 then ($q / $h | round) else null end)}'
```
Stage 1 sold alone has no `$REC/estimate.json`: compare with `stage1.hours.point` of `$PRIV/out/estimate.json` instead. The coefficients in `toolkit/estimate.toml` change only by a pipeline PR after at least 3 engagements; no toolkit command does this.

---

## 4. Gates

`$REC/gates.json` records each approval (docs/contracts.md section 15). The launcher embeds it (2(f)), and the driver reads it as `args.gates`: for clients it refuses a stage whose prerequisite gate is not approved, and S2 without the S1 deposit. Every engagement writes it, demos included: `spec-check` in S2, S4, S5 and S6 compares `analysis/rules.json` and `rules.md` with the S1 hashes (or the latest approved change order), and fails without them.

| Key | Fields | Written |
|---|---|---|
| `S0` to `S6` | `approved` (YYYY-MM-DD) | at each gate |
| `S1` | also `signed_by` ("name, title"; demos: "Thomas Sodon, demo"), `rules_sha256`, `rules_md_sha256`, `deposit_received` | at the S1 gate; `deposit_received` true only once the deposit is paid (demos: true) |
| `CO-NNN` | `approved`, `rules_sha256`, `rules_md_sha256` | at a change-order gate (section 5) |
| `S3` | also `requote` `{approved, usd}` | at the S3 client gate, only when Stage 1 was sold alone (no S1 gate, so no change order) and the work was re-quoted; the new price also goes into `commercial.quote` |

Approve a stage:
```
. $HOME/engagements/<id>/run.env && F="$REC/gates.json" && { test -f "$F" || echo '{}' > "$F"; } && jq --arg s "$S" --arg d "$(date -u +%F)" '.[$s] = ((.[$s] // {}) + {approved: $d})' "$F" > "$F.new" && mv "$F.new" "$F"
```
S1, with the hashes taken at the target commit the client signed (the head SHA from the gate). The committed rules.json keeps `signed: null`; the signature lives only here:
```
. $HOME/engagements/<id>/run.env && TSHA=<signed target commit> && RJ=$(git -C "$TREPO" show "$TSHA:${TPFX}analysis/rules.json" | sha256sum | cut -d' ' -f1) && RM=$(git -C "$TREPO" show "$TSHA:${TPFX}analysis/rules.md" | sha256sum | cut -d' ' -f1) && F="$REC/gates.json" && { test -f "$F" || echo '{}' > "$F"; } && jq --arg d "$(date -u +%F)" --arg by "<name, title>" --arg rj "$RJ" --arg rm "$RM" --argjson dep <true | false> '.S1 = {approved: $d, signed_by: $by, rules_sha256: $rj, rules_md_sha256: $rm, deposit_received: $dep}' "$F" > "$F.new" && mv "$F.new" "$F"
```
When the deposit arrives later (clients):
```
. $HOME/engagements/<id>/run.env && F="$REC/gates.json" && jq '.S1.deposit_received = true' "$F" > "$F.new" && mv "$F.new" "$F" && git -C "$REC" add gates.json && git -C "$REC" commit -m "$ID: S1 deposit received"
```
In a client session these blocks run as files through mp-run (1.9).

Other gate-time records:
- **S0:** approve the recorded counts in `inventory.lock.json` and the hashes in `source.lock.json` (clients and new sources). They are compared on every later run; never change them without the user.
- **S0 (clients, Stage 1 sold alone):** set `commercial.quote` and `commercial.budget_hours` from `stage1` in `$PRIV/out/estimate.json` (design zone) and add `stage1` to `commercial.purchased`, as the S0 client gate says. Commit in the records repo.
- **S1 (clients):** set `commercial.budget_hours`, `commercial.quote` (the fixed price before credit) and `commercial.assessment_credit_usd` in engagement.json from `estimate.json` and `quote.md`, and add sold offers to `commercial.purchased`. Commit in the records repo.
- **S3 (clients):** before the freeze window, store the Stage 1 target database URL under `secrets.stage1_target_url_ref` (1.6). The cutover in the window writes `$REC/stage1-cutover-report.md` (counts only), from `mp-data stage1 <REC>/engagement.json --target-db secret:stage1_target_url_ref --dataset fresh-copy --verify --require-rows --report <REC>/stage1-cutover-report.md`, run by the user in a plain terminal after the load (S3 client gate). Commit it in the records repo with the gate record.
- **S5 (clients):** before the gate, store the staging database URL under `secrets.staging_db_url_ref` (1.6). The import rehearsal into `mpdata_<db_name>_gate` and the staging load follow the S5 client gate, in a plain terminal with only the env file sourced. For the rehearsal the superuser creates `mpdata_<db_name>_gate` owned by `mp_runner`, and the app gets only the runner URL as `DATABASE_URL` (read from data.env in a subshell); never source data.env into the app's shell. When the counts match, unset `DATABASE_URL` and drop the database (purge drops a left-over one). The app is agent-written code running outside any jail there, a recorded residual risk (1.9).
- **S6 (clients, Access kinds):** before the go-live load, store the production database URL under `secrets.production_db_url_ref` (1.6). The load follows the Go-live and smoke checklist section of the target's `docs/runbook.md`, run by the user in a plain terminal; its check writes `$REC/golive-report.md` (counts only). Commit it in the records repo with the gate record.
- **Gate kind:** `gate.kind` is `demo` when `commercial.tier` is `demo`, else `client`. The driver picks the matching checklist.
- Squash-merge a stage's PRs only after its `gates.json` entry is recorded.

---

## 5. Change orders

After the S1 gate, `analysis/rules.*` change only through an approved change order. Change orders need an approved S1 gate (the driver refuses one without it); when Stage 1 was sold alone, the S3 gate re-quotes instead (`S3.requote`, section 4). Run change orders between stages: finish or checkpoint the current stage first.
1. Pick the next id (a missing `change-orders/` directory means none yet):
```
. $HOME/engagements/<id>/run.env && ls "$REC/change-orders/" 2>/dev/null | grep -E '^CO-[0-9]{3}\.md$' | sort | tail -1
```
   Add 1 (start at CO-001).
2. Do section 2(a) with `S=S1` (the previous S1 branches were deleted at the merge or, when unchanged, after the gate, so new ones start from `origin/main`). Then write the request file (docs/contracts.md section 15). You write the title, `requested` and `## Request` (the client's request in plain words, rules only, no data values); S1.change_order writes the rest:
```
. $HOME/engagements/<id>/run.env && mkdir -p "$REC/change-orders" && printf '# CO-NNN: <short title>\nrequested: %s\nbase_rules_sha256:\nrules_sha256_after:\nrules_md_sha256_after:\napproved:\n\n## Request\n<the request>\n\n## Rules added\n## Rules changed\n## Objects changed\n<!-- estimate:begin -->\n<!-- estimate:end -->\n' "$(date -u +%F)" > "$REC/change-orders/CO-NNN.md"
```
   Commit it (demos: on the S1 stage branch, then push; clients: in the records repo), then do 2(b) to 2(h) with `change_order: "CO-NNN"` in `args-S1.json` for the dry run and the real run, and `since` set to the title. Only `S1.change_order` and the reviewers run. It adds or amends rules with new rule ids, updates keep/drop, and fills `CO-NNN.md` with the hashes and the price delta from `estimate --delta`. Until the change order is approved, `spec-check` reports it as pending and exits 0.
3. Gate: the client approves `CO-NNN.md` and its price. Set `approved: YYYY-MM-DD` in `CO-NNN.md`, and record `"CO-NNN": {approved, rules_sha256, rules_md_sha256}` in gates.json with the hashes at the approved target commit (same command as S1 in section 4, with key `CO-NNN` and without `signed_by` and `deposit_received`). Add the hours to `commercial.budget_hours`. Then squash-merge as in 2(i).
4. If a later stage branch already exists (a checkpoint), bring the change in and rerun the affected tasks. First run the first command of 2(a) with `S=<the later stage id>` and its checkout block (clients: in the plain terminal), so run.env names the later stage and both repos are on its checkpoint branches. Then:

   Demos: merge, guard, push:
```
. $HOME/engagements/<id>/run.env && git -C "$TREPO" fetch origin && git -C "$TREPO" merge origin/main -m "Merge CO-NNN into $S" && git -C "$PIPE" fetch origin && git -C "$PIPE" merge origin/main -m "Merge CO-NNN into $S"
mp-data guard <REC>/engagement.json --repo <PIPE> --repo <TREPO> --history
. $HOME/engagements/<id>/run.env && git -C "$TREPO" push origin "$ID/$S-$SLUG" && git -C "$PIPE" push origin "eng/$ID/$S-$SLUG"
```
   Clients, in a plain terminal:
```
. $HOME/engagements/<id>/run.env && rgit "$TREPO" fetch origin && rgit "$TREPO" merge origin/main -m "Merge CO-NNN into $S" && mp-data push "$REC/engagement.json" --branch "$ID/$S-$SLUG" && rgit "$PIPE" fetch origin && rgit "$PIPE" merge origin/main -m "Merge CO-NNN into $S" && rgit "$PIPE" push origin "eng/$ID/$S-$SLUG"
```
   Then continue as in 2(j) with `since: "CO-NNN approved"`.

---

## 6. S6 demo gate: walkthrough, pre-flip checks and publishing

The northwind-demo S6 gate makes the demo repo public. Do it before invoicegen-demo starts, so the demo repo has no open stage branches. The invoicegen-demo S6 gate repeats steps 1 to 10 and 12, and finds the repo already public at step 11. Ask the user before each GitHub action. Every check must pass.

1. **Merge.** 2(i) records the gate first (steps 5 and 6: gates.json `S6` and the log row on the pipeline stage branch), then squash-merges both S6 PRs (step 7). Everything below works on the merged main.
2. **Record the walkthrough** from the merged main, in a plain terminal (seed-1 data only; the video stays in the design zone and is never committed):
```
. $HOME/engagements/<id>/run.env && git -C "$TREPO" switch main && git -C "$TREPO" pull --ff-only && . "$ENVF" && export MP_DB_SUFFIX=s6_gate && cd "$TARGET/app" && E2E_VIDEO_DIR="$PRIV/synthetic/media/walkthrough" uv run pytest -q --create-db -m walkthrough tests/e2e && test -s "$PRIV/synthetic/media/walkthrough/walkthrough.webm"
```
   The user narrates it with `docs/walkthrough.md` and uploads it unlisted. Keep the link for step 10.
3. **Fetch everything:**
```
. $HOME/engagements/<id>/run.env && git -C "$TREPO" fetch --all --prune --tags
```
4. **Delete stage branches whose PR was merged** (list them first and ask):
```
. $HOME/engagements/<id>/run.env && for b in $(git -C "$TREPO" branch -r --format='%(refname:lstrip=3)' | grep -v -x -e HEAD -e main); do n=$(gh pr list -R "$TREPO_GH" --head "$b" --state merged --json number --jq length); test "$n" -ge 1 && echo "merged: $b" || echo "NOT MERGED: $b"; done
. $HOME/engagements/<id>/run.env && git -C "$TREPO" push origin --delete <each merged branch>
```
5. **No unmerged branch is left:**
```
. $HOME/engagements/<id>/run.env && git -C "$TREPO" fetch --prune && test -z "$(git -C "$TREPO" branch -r --no-merged origin/main)"
```
6. **No copied source anywhere in history** (every blob reachable from any ref). One standalone data-plane call, paths typed out:
```
mp-data guard <REC>/engagement.json --repo <PIPE> --repo <TREPO> --history
```
7. **No forbidden file name ever committed:**
```
. $HOME/engagements/<id>/run.env && git -C "$TREPO" log --all --name-only --format= | sort -u | grep -Ei '\.(accdt|accdb|accde|mdb|mde|laccdb|ldb|mdw|xlsm|xlsb|xls|xlam|bas|cls|frm|frx|msg|bak|pdf|eml|xlsx)$|^(private|data|vendor)/'; test $? -eq 1
```
8. **Actions logs and artifacts** become public too. CI never sees engagement data, but delete every run from the private period anyway (list, ask, delete):
```
. $HOME/engagements/<id>/run.env && gh run list -R "$TREPO_GH" --limit 1000 --json databaseId,workflowName,headBranch,createdAt --jq '.[] | "\(.databaseId) \(.workflowName) \(.headBranch) \(.createdAt)"'
. $HOME/engagements/<id>/run.env && gh run list -R "$TREPO_GH" --limit 1000 --json databaseId --jq '.[].databaseId' | while read -r id; do gh run delete "$id" -R "$TREPO_GH"; done
```
9. **PR titles, bodies and comments** become public. Read them and fix any that mention client material, private paths or internal notes (`gh pr edit <number> -R "$TREPO_GH" --body-file <file>`):
```
. $HOME/engagements/<id>/run.env && gh pr list -R "$TREPO_GH" --state all --limit 200 --json number,title,body,comments
```
10. **Publish branch `<id>/S6-publish`.** From `origin/main`, fill the Pre-flip checks table of `<target>/docs/legal-check.md` (S6.handover wrote the file: each check, the command, the result, the date and the `origin/main` commit checked) and its Decision section, and replace the video line in the `<id>` section of the root README with the unlisted link. Commit, run the guard (step 6), push, open a PR, let the guard workflow pass (wait in a background call), and squash-merge it:
```
. $HOME/engagements/<id>/run.env && git -C "$TREPO" fetch origin && git -C "$TREPO" switch -c "$ID/S6-publish" origin/main
```
   Edit the two files with the Edit tool, then:
```
. $HOME/engagements/<id>/run.env && git -C "$TREPO" add "${TPFX}docs/legal-check.md" README.md && git -C "$TREPO" commit -m "$ID: pre-flip checks and walkthrough link" -m "<attribution lines>"
mp-data guard <REC>/engagement.json --repo <PIPE> --repo <TREPO> --history
. $HOME/engagements/<id>/run.env && git -C "$TREPO" push -u origin "$ID/S6-publish" && gh pr create -R "$TREPO_GH" --base main --head "$ID/S6-publish" --title "$ID: pre-flip checks and walkthrough link" --body "Pre-flip check results in docs/legal-check.md and the unlisted walkthrough link."
. $HOME/engagements/<id>/run.env && gh pr checks "$ID/S6-publish" -R "$TREPO_GH" --watch --fail-fast --interval 30
. $HOME/engagements/<id>/run.env && gh pr merge "$ID/S6-publish" -R "$TREPO_GH" --squash --delete-branch
```
   Then repeat steps 3, 5 and 7.
11. **Flip** (ask first), only when every check passed and the Decision says publish. Check the visibility first:
```
. $HOME/engagements/<id>/run.env && gh repo view "$TREPO_GH" --json visibility -q .visibility
```
   `PUBLIC`: an earlier demo's S6 gate already flipped it; nothing to flip. `PRIVATE`: gh 2.46 needs no confirmation flag, newer releases require `--accept-visibility-change-consequences`; this uses the flag when `gh repo edit --help` lists it:
```
. $HOME/engagements/<id>/run.env && if gh repo edit --help | grep -q -- '--accept-visibility-change-consequences'; then gh repo edit "$TREPO_GH" --visibility public --accept-visibility-change-consequences; else gh repo edit "$TREPO_GH" --visibility public; fi
```

**If any check fails**, do not flip. Publish a single squashed commit of main to a fresh public repo instead (ask first):
```
gh repo rename access-excel-to-django-private -R thomas-sodon/access-excel-to-django --yes
d=$(mktemp -d) && git -C $HOME/sourcecode/access-excel-to-django archive origin/main | tar -x -C "$d" && git -C "$d" init -b main && git -C "$d" add -A && git -C "$d" commit -m "access-excel-to-django: public release of $(git -C $HOME/sourcecode/access-excel-to-django rev-parse --short origin/main)" -m "<attribution lines>" && gh repo create thomas-sodon/access-excel-to-django --public --source="$d" --push
mv $HOME/sourcecode/access-excel-to-django $HOME/sourcecode/access-excel-to-django-private && git -C $HOME/sourcecode/access-excel-to-django-private remote set-url origin https://github.com/thomas-sodon/access-excel-to-django-private.git && cd $HOME/sourcecode && gh repo clone thomas-sodon/access-excel-to-django
```
The `remote set-url` matters: after the rename, the old clone's `origin` URL names the new public repo, so a push from it would publish the private history. The new clone has no pre-commit hook yet; the `hooks` item installs it at the next 2(c). This is the only version of the fallback: the S6 demo gate points here.

12. **Close out the log** (ask first; after step 11 or the fallback). Start a branch in the pipeline repo:
```
. $HOME/engagements/<id>/run.env && git -C "$PIPE" fetch origin && git -C "$PIPE" switch -c "eng/$ID/S6-closeout" origin/main
```
   Add the minutes of steps 2 to 11 to `human gate min` and `human other min` of the S6 row in `engagements/<id>/log.md` (Edit tool). Then commit, push and open the PR:
```
. $HOME/engagements/<id>/run.env && git -C "$PIPE" add "engagements/$ID/log.md" && git -C "$PIPE" commit -m "$ID: S6 gate minutes" -m "<attribution lines>" && git -C "$PIPE" push origin "eng/$ID/S6-closeout" && gh pr create -R "$PIPE_GH" --base main --head "eng/$ID/S6-closeout" --title "$ID: S6 gate minutes" --body "Minutes of the S6 post-merge steps (runbook section 6)."
```
   Wait for the checks in a background call, then merge:
```
. $HOME/engagements/<id>/run.env && gh pr checks "eng/$ID/S6-closeout" -R "$PIPE_GH" --watch --fail-fast --interval 30
. $HOME/engagements/<id>/run.env && gh pr merge "eng/$ID/S6-closeout" -R "$PIPE_GH" --squash --delete-branch
```
   Then run the budget check (section 3).

**Once the repo is public**, every push to it runs the guard with `--history` first: integrate and checkpoint do it for demos, and you do it before your own pushes (rules). invoicegen-demo then works in public: its stage branches and checkpoints are public as soon as they are pushed.

---

## 7. Retention and purge

Client data is deleted `data_policy.retention_days_after_acceptance` days (default 30) after the last purchased stage's gate, unless `care_plan.private_data` is `keep-encrypted-for-care-plan` with the client's written consent.

Purge date:
```
. $HOME/engagements/<id>/run.env && LAST=$(jq -r '[to_entries[] | select(.key | test("^S[0-6]$")) | .value.approved] | max' "$REC/gates.json") && DAYS=$(jq -r '.data_policy.retention_days_after_acceptance' "$REC/engagement.json") && date -u -d "$LAST + $DAYS days" +%F
```
Record the date in `docs/data-retention.md` at the S6 gate, or in the log.md row of the last gate when that is S0, S1 or S3 (health-check, assessment or Stage 1 as the last purchased offer; their gate checklists say so). On the date, in a plain terminal (mp-data reads the database superuser URLs from data.env):
```
. $HOME/engagements/<id>/run.env && mp-data purge "$REC/engagement.json" --dry-run
. $HOME/engagements/<id>/run.env && mp-data purge "$REC/engagement.json"
```
`purge` deletes everything under `$PRIV` (the directories its jail mounts stay, empty), drops `mpdata_<db_name>_*` (a left-over `mpdata_<db_name>_gate` too), `mpdata_test_<db_name>_*`, `<db_name>_dev`, `<db_name>` and `test_<db_name>_*` on every running local database server, keeps the databases of another engagement whose db_name starts with this one plus `_`, names servers that do not answer as not checked, lists the Windows-box copies in `environment.windows_box.copies`, and writes `$REC/destruction-record-<YYYY-MM-DD>.md`. Then, in the plain terminal:
- delete the listed Windows-box copies by hand and add a line to the destruction record;
- commit the records: `. $HOME/engagements/<id>/run.env && rgit "$REC" add -A . && rgit "$REC" commit -m "$ID: data purged"`;
- remove what purge left under `$PRIV`, the pipeline worktree, the session settings, the secrets that this engagement's refs name (each `env:NAME` line in data.env; the command prints each `pass:<path>` ref, and you run `pass rm <path>` for it) and, for target.mode github, the target clone (the client keeps the repo and revokes the token at the S6 gate; a local-mode target repo is kept with the records):
```
. $HOME/engagements/<id>/run.env && test "$PRIV" = "$HOME/engagements/$ID/private" && rm -rf -- "$PRIV" && rgit $HOME/sourcecode/modernization-pipeline worktree remove "$PIPE" && rm -f $HOME/engagements/$ID/client-session.json && { test "$TMODE" != github || rm -rf "$TREPO"; } && for n in $(jq -r '[.secrets, .source] | [.. | strings | select(test("^env:[A-Z_][A-Z0-9_]*$")) | ltrimstr("env:")] | unique | .[]' "$REC/engagement.json"); do sed -i "/^export $n=/d" $HOME/.config/modernization/data.env; done && jq -r '[.secrets, .source] | [.. | strings | select(startswith("pass:"))] | unique | .[]' "$REC/engagement.json"
```
The pipeline clone's shared `.git/hooks/pre-commit` may still name the removed worktree's `.venv`; the next 2(c) `hooks` item reinstalls it (or run `cd $HOME/sourcecode/modernization-pipeline && uv run pre-commit install` now).
Keep the records repo (engagement, quote, log, gates, destruction record). Demos: the S6 gate runs `mp-data purge ... --dry-run` to rehearse; the demo data is a public sample and may stay.

---

## 8. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `uv: command not found`, or uv is not 0.13.1 | Environment items `env_file` (PATH) and `uv`; rerun 2(c) |
| `jq: command not found`, or jq older than 1.7 | Item `jq`. On a new machine the runbook needs jq before 2(c): install it once by hand (0.6) |
| No Python 3.13, or the app refuses Python 3.14 | Item `python313` |
| JPype finds no JVM, `JAVA_HOME` empty, `javac: not found` | Item `jdk` (every stage and source kind) |
| `libodbc.so.2`, `libltdl.so.7` or `libpangoft2-1.0.so.0` cannot be opened; WeasyPrint or mssql-django fails to import | Item `syslibs` |
| bwrap fails (for example `setting up uid map: Permission denied`) or is missing | Item `bwrap` has no fix: ask the user to run `sudo apt-get install bubblewrap`. On Ubuntu 24.04 and later with `sysctl kernel.apparmor_restrict_unprivileged_userns` printing 1, bwrap needs an AppArmor profile named bwrap: look for an existing one first (Ubuntu 26.04 ships `/etc/apparmor.d/bwrap-userns-restrict`), and write one only as the item's note says (0.9) |
| `toolkit/tests/test_mp_run.py` reports skipped | Expected inside mp-run (`MP_SANDBOX` is `mp-run`; a nested bwrap is refused). On a client host it runs in the plain terminal, in probe step 6 (1.13) |
| Connection refused on 127.0.0.1:55432, or `PG_URL` empty | Item `postgres` |
| `pg_hba.conf rejects connection` for `mp_agent` on an `mpdata_` database, or for `mp_runner` on a database other than `postgres` and the `mpdata_` ones | Expected: agents (PG_URL, `mp_agent`) never reach engagement rows, and the runner login that mp-data gives agent-written code reaches only those. Only mp-data and the user (data.env, plain terminal) use the `mpdata_` databases |
| Connection refused on 127.0.0.1:14330, `MSSQL_URL` empty, or login failed for `mp_agent`, `mp_runner` or `sa` | Item `mssql` |
| data.env lost, or a database line missing from it | Rerun 2(c) (clients: plain terminal). The `postgres` fix sets new passwords over the container's local socket, so it repairs itself. SQL Server keeps the old sa password: run `docker rm -f mp-mssql22` first (the container has no volume; its databases can be rebuilt). Store the engagement's `env:` secrets again (1.6) |
| A merged change to `scripts/mp-data`, `scripts/mp-run` or `scripts/mp-bash-hook` has no effect | The `mp_data` check only tests that the copies exist. Demos: run `"$PIPE/scripts/install-mp-data.sh"` after 2(a) (2(c)). Clients: pin the new release (1.12) |
| `mp-data: command not found` or `mp-run: command not found` | Item `mp_data` (after S0 has merged once; the first S0 builds it). In a session: start it with the env file sourced (0.8, 1.13) so `$HOME/.local/bin` is on PATH |
| mp-data exits 2 with `no reviewed toolkit: run mp-data pin <sha>`, or `mp-data pin` refuses | Client engagements need a pin (1.12). pin runs only in a terminal, inside a git checkout (cd into the pipeline clone first), and only for a sha that its `origin/main` holds |
| An mp-data summary says `not-built` in the S0 prepare list | Expected in bootstrap mode until the S0 task that builds that command has merged |
| mp-data exits 2 because a database name lacks the `mpdata_` prefix, or because an `mpdata_` database belongs to someone other than `postgres`, `sa` or `mp_runner` | Expected guard: engagement rows enter only `mpdata_` databases that the superuser or the runner owns. Use the names in pipeline.json `stack.database_urls.names`. An `mpdata_` database with another owner was not made by mp-data or a gate step: show it to the user, who drops it in a plain terminal |
| A data-plane call fails writing a file | mp-data's toolkit child may write only this engagement's data zone, `$TMPDIR` and the call's declared outputs (`--out`, `--report`, `--junit-dir`, `--matrix`). Fix the pipeline.json line or the code it runs on a pipeline branch; never run the command outside mp-data |
| `mp-data equivalence` exits 2 naming `cd <TARGET>/app && uv sync --locked` | The app's `.venv` is missing; mp-data's Django cells use its python because there is no uv inside the jail. Run the prepare list (2(g)): the S5 and S6 lists start with that line |
| `mp-data push` exits 2 naming the target repo's git config or its `origin` | The repo's own config sets a `url.*.insteadOf`, `url.*.pushInsteadOf`, `remote.origin.proxy` or `http.*` proxy or ssl key, or `origin` (url or pushurl) is not `https://github.com/<target.repo>`. Remove the key (`rgit "$TREPO" config --unset-all <key>`) or fix `origin` in a plain terminal (1.6) |
| A forbidden file was committed without a hook error; `.git/hooks/pre-commit` missing | Item `hooks` |
| Playwright cannot launch Chromium | Item `chromium` (S5, S6; never `--with-deps`) |
| S2 notes say the LibreOffice cross-check was skipped | Item `libreoffice` is optional; nothing to do |
| `Cannot connect to the Docker daemon` in the preflight or your own command | Item `docker` has no fix: ask the user to start rootless Docker (`systemctl --user start docker`) |
| `Cannot connect to the Docker daemon` inside mp-run or from an agent | Expected: mp-run hides Docker. Agents use PG_URL and MSSQL_URL; containers are the environment's job (2(c)) |
| `gh auth status` fails | Item `gh` has no fix: ask the user to run `gh auth login --insecure-storage` |
| `git push` or `gh` fails to authenticate inside mp-run but works in a plain terminal | gh keeps its token in the system keyring, which mp-run hides with `$XDG_RUNTIME_DIR`. Ask the user to log in again with `gh auth login --insecure-storage`; `gh auth status` must name `hosts.yml` (1.9). Or the clone's origin is an SSH URL: `git -C <repo> remote set-url origin https://github.com/<owner>/<repo>.git` |
| `agent cap N reached`, or the run is refused as too large | Raise "Dynamic workflow size" (0.7); pass `max_agents` = the dry run's `estimate.worst` |
| An agent waits for permission on a path in the target repo or `$HOME/engagements/<id>` | The session lacks an `--add-dir`. Restart it (0.8 or 1.13) and continue in a new session (2(j)) |
| A resumed run returns the same blocked result at once | It came from a resume cache. Regenerate the launcher with `--prior` and pass `attempt` N+1, `rerun` and `since` (2(j)) |
| `resumeFromRunId` is unknown | Resume ids live in one session. Regenerate the launcher with `--prior` and continue (2(j)) |
| `make-launcher:` exits 2 | It names the cause: a missing or invalid file, a file key (`pipeline`, `engagement`, `gates`, `prior`, `prior_items`) in `args-<S>.json` (remove it; the launcher reads those from disk), or a launcher over 500000 bytes (show the user; never paste the files into `args` instead) |
| The real run refuses to start and names `prepared` | Run the prepare list (2(g)), then put `prepared: "<S>"` in `args-<S>.json` and regenerate the launcher (2(f)) |
| `<n> exit check(s) failed` with every task done | 2(j) step 2: run the line, rerun its owning task |
| Dry-run problem `<where>: mp-data must be a standalone data-plane call` | A pipeline.json line combines mp-data with other shell. Split it into separate lines on a pipeline branch (docs/contracts.md section 1); never run the combined line |
| Dry-run problem `remove stage preflight; environment[].stages is the source` | Delete that stage's `preflight` key in pipeline.json; the environment list decides |
| Dry-run problem `<where>: command spans more than one line (the client Bash hook blocks newlines)` | Put that command on one line in pipeline.json on a pipeline branch. Environment commands are exempt |
| Dry-run problem about a `<...>` marker or an unresolved `{{...}}` | Fill engagement.json; run `uv run toolkit engagement-lint` (2(b)) |
| `not in the purchased offers`, `gate Sx not approved in gates.json`, `S2 needs the S1 deposit` | Update `commercial.purchased` or `gates.json` (section 4) after the user confirms |
| `client engagement: host not attested`, or consent or AI terms missing | Sections 1.8 and 1.11 |
| Client session: a Bash call is refused with `client session: run shell commands as mp-run ...` | Expected for anything that is not one mp-run or mp-data call. Wrap it as in 1.9, or run it in a plain terminal when it needs Docker, data.env or the push token |
| Client session: a plain command such as `ls` runs without that message | Stop. The hook is missing or the settings did not load: run the `mp_data` item (2(c)), regenerate the settings (1.10), restart with `--settings` (1.13) and repeat the probe |
| An mp-data line run inside mp-run cannot reach a database or a secret | mp-run hides data.env. Run mp-data directly, as its own Bash call |
| git inside mp-run prints `could not write config file`, or `git config` or `git remote` fails there | Expected: mp-run binds the git config of the pipeline and target repos read-only. A `git push -u` still pushed. Make config and remote changes in a plain terminal with `rgit` (1.6) |
| `could not read Username` when fetching a client target repo | Fetch with `rgit` in a plain terminal (1.3, 1.6); the token is unreadable in sessions and inside mp-run |
| `gh pr checks`: no checks reported | Both repos have CI from S0 on: run it again after a minute, then `gh run list -R <repo> --branch <branch>`; a missing run is a failure |
| `gh pr merge`: head commit does not match | Something was pushed after the gate. Review the new commits with the user, show the gate again, merge with the new SHA |
| `gh pr create`: no commits between main and the branch | That repo had no changes in this stage; skip its PR |
| `dirty tree` in 2(a) | Commit your own edits (records, answers, dependencies) on the right branch. Never stash or delete agent work you have not reviewed. A repo already on the stage branch is skipped: on a resume its changes under not-done tasks' owned paths are their partial work; keep them. |
| `checkpoint.unowned_changes` after a not-ready run | Files of blocked or skipped tasks (in the first S0 before guard exists, every target-repo file): keep them for the next attempt, never revert them |
| Integrate reports `unowned_changes` | In a ready-looking integrate: a path outside every owned path changed. Show `git status`; revert it or commit it yourself with the user's consent; continue (2(j)) |
| `mp-data push` or `mp-data gh` fails with 401 or 403 | Token expired or wrong scope (`secrets.github_push.expires`). The client issues a new one; replace its value in data.env or `pass` (1.6) |
| `spec-check` fails in S2, S4, S5 or S6 | `analysis/rules.*` changed after signing, or the S1 hashes in gates.json are missing or wrong. Recompute them at the signed commit (section 4); real changes need a change order (section 5) |
| `fetch --verify` reports a mismatch | The source changed. Stop and ask the user; never update pins or locks to make it pass |
| `inventory --expect` fails while `fetch --verify` passes | A counting difference, not a changed source. Show the key, expected and counted values and the S0.parse notes against `expected_inventory._count_rules` and `_vba_line_rule`. The user either reruns `S0.parse` and `S0.healthcheck` with `since` naming the rule, or, after re-checking the measurement, corrects `expected_inventory` with a dated `_measured` note; never edit a number just to make a run pass |
| The guard exits 2 because the source is missing | The data zone was lost: run the prepare list (2(g)). In S0, rerun the stage |
| Tests in different agents fight over one database | A command ran outside its given form. Run commands exactly as the dry run or prompt gives them: the `--slot` of mp-run and mp-data sets `MP_DB_SUFFIX` |
| A blocked task asks for `dependency: <spec>` | Add it on the stage branch (2(j)) and rerun that task |
