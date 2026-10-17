// run-stage: runs ONE stage of the modernization pipeline (pipeline.json version 2) for ONE engagement.
// It is a Claude Code Workflow-tool script: plain JavaScript, no file or Node API, no Date.now(),
// Math.random() or argless new Date(). Tested with mocked agents by tests/run-stage.test.mjs.
// In the pipeline repo it lives at .claude/workflows/run-stage.js. docs/runbook.md says when to call it;
// docs/contracts.md documents placeholders, anchored commands and the files named here.
//
// The orchestrating session (rooted at the pipeline checkout, started with the env file sourced so that
// mp-data and mp-run resolve as bare words, for demos and clients alike) runs the environment preflight
// (for client engagements the operator runs it in a plain terminal), writes {{p.records}}/paths.local.json,
// runs the stage's prepare list, then calls the driver. Workflow scripts cannot read files, so everything
// comes in `args`. pipeline.json, engagement.json and gates.json (and, when continuing, prior and
// prior_items) are too large to type into a Workflow call: the orchestrator embeds them in a per-stage
// launcher, $HOME/engagements/<id>/launch-<stage>.workflow.js, written by .claude/workflows/make-launcher.py
// with pipeline.json sliced to this stage (docs/runbook.md 2(f), docs/contracts.md#launcher), which calls this file
// with `workflow({ scriptPath: "<pipeline>/.claude/workflows/run-stage.js" }, { ...args, ...FILES })`.
// The args this script receives:
//
//   {
//     pipeline:     <parsed pipeline.json>                          required
//     engagement:   <parsed engagement.json, as on disk>            required; the driver fills {{p.*}} in it
//     stage_id:     "S0" .. "S6"                                    required
//     paths: { pipeline, target_repo, target, private, env_file, records? }
//                   required, absolute, no ~. records defaults to <pipeline>/engagements/<id> (demos);
//                   client engagements (bootstrap false) must pass it ($HOME/engagements/<id>/record).
//     dry_run:      true      plan only: spawns nothing and returns the plan below
//     prepared:     "S2"      required for a real run: the stage whose environment preflight and prepare list just ran
//     items:        { "<fan-out id>": [{ id, title, scope }] }   from the dry run's items_from
//     prior:        { "<task or item id>": <result> }  `results` of {{p.records}}/runs/<stage>.json
//                   (a change-order run: runs/S1-CO-NNN.json)
//     prior_items:  { "<fan-out id>": [items] }        `items` of the same file
//     rerun:        ["S2.reference", "S1.area.orders"]  fresh agents for these (a fan-out id means all its items)
//     attempt:      2         default 1;   since: "answers.md: Q-orders-002 answered"
//                   (every prompt's preamble names the attempt number, so a raised attempt changes every
//                   prompt and a Workflow resume returns nothing from its cache; the "Previous result ...
//                   What changed" text goes only to rerun tasks and to tasks whose prior result was not done)
//     gates:        <parsed gates.json>    client engagements: approved earlier gates and the S1 deposit
//     host_attested: true     client engagements: the host has $HOME/.config/modernization/client-host
//     change_order: "CO-001"  S1 only: run S1.change_order (and the review) instead of the assessment tasks
//     commit_trailer: "<lines>"   appended to every commit message
//     max_agents:   20        cap on spawned agents; reaching it blocks the task (the checkpoint agent is exempt)
//     allow_over_budget: true    let a fan-out run more than its max_items items
//     checkpoint:   false     no WIP checkpoint agent on a not-ready run (default true)
//   }
//
// Every acceptance, verify, exit and prepare command, the dry run's items_from commands, and the driver's
// own integrate and checkpoint checks, is anchored for one agent slot (contracts.md#command-forms); each is
// one line, because the client Bash hook blocks newlines. <slot> is the agent label
// in lower case with every run of characters outside [a-z0-9] replaced by "_" and leading or trailing "_"
// dropped; a slot longer than 24 characters becomes its first 17 characters, "_" and a 6-character hash:
// S5.feature.orders -> s5_feature_orders, S5.review:r1 -> s5_review_r1.
// A command takes one of three forms:
//   data-plane call: a command that starts with "mp-data " and has no shell operator, redirection,
//     substitution, subshell, brace group, cd, if or for, and no $ other than $MP_DB_SUFFIX. Quoted
//     arguments are allowed. It runs unwrapped, as one simple command:
//       mp-data --slot <slot> <rest>              (every $MP_DB_SUFFIX replaced by the literal slot)
//   any other command:
//       mp-run --eng <records>/engagement.json --slot <slot> -- '<cmd>'     (each ' in <cmd> written '\'')
//     mp-run sources the env file, exports MP_DB_SUFFIX=<slot>, changes to the pipeline checkout unless
//     <cmd> starts with "cd ", and runs <cmd> under bubblewrap with the data zone, other engagements,
//     data.env, the pass store, the Docker socket and $XDG_RUNTIME_DIR (keyring, ssh and gpg agents)
//     hidden, and the installed tools, the Claude settings, .git/hooks and .git/config read-only; git and
//     gh inside it authenticate only with gh's file-stored token (hosts.yml). Demos therefore meet the
//     client restriction from S1 on.
//   any other command in an S0 run in bootstrap mode (mp-run does not exist yet):
//       . <env_file> && export MP_DB_SUFFIX=<slot> && cd <pipeline> && { <cmd>; }
//     without the "cd <pipeline> &&" part when <cmd> starts with "cd ". The braces keep
//     "cmd; test $? -eq 1" from passing when the env file or the cd fails.
// A pipeline.json command that invokes mp-data but is not a data-plane call is a problem, environment
// commands included (contracts.md §3). An agent command (acceptance, verify, exit, prepare) or an items_from
// command that spans more than one line is a problem too. Environment check, fix and when commands are
// exempt from that one-line rule and are never anchored: they need Docker and data.env, so the
// orchestrator runs them itself (in a plain terminal for client engagements).
//
// Returns, dry run:
//   { engagement, stage, dry_run: true, applies: true, mode, attempt, change_order, branches,
//     problems[],            empty when a real run would start; a real run throws with this list
//     warnings[],            inconsistencies worth fixing that do not stop the run
//     tasks[{ id, kind, applies, reason?, depends_on, owns, reused }], tasks_not_applicable[ids],
//     estimate{ typical, worst }, agent_budget, over_budget,
//     preflight[environment ids], environment[{ id, check, fix?, when?, optional?, writes?, note? }]
//                            pipeline.json environment items whose stages include this stage and whose
//                            kinds (if any) include the source kind, in environment order; filled, never
//                            anchored; when is not evaluated: the orchestrator runs it first and skips the
//                            item when it exits non-zero
//     prepare[anchored commands], exit_criteria[anchored commands],
//     items_from{ <fan-out id>: { file, jq, cmd } } (cmd anchored), items{ <fan-out id>: [items] },
//     gate{ name, kind: demo|client, checklist[], est_minutes } }
//   Fan-out item problems (from args.items or prior_items) appear in problems; in a real run they block the fan-out.
// Returns, stage does not apply to the source kind (S3 for xlsm):
//   { engagement, stage, not_applicable: true, ready: true, reason }; a dry run adds dry_run: true, applies: false,
//   problems and warnings (a real run with problems throws).
// Returns, real run:
//   { engagement, stage, mode, attempt, change_order, branches, ready, blockers[],
//     tasks[{ id, status, summary, notes, reused }],
//     results{ <task or item id>: result incl. files_changed and acceptance },   pass back as args.prior
//     items{ <fan-out id>: [items] },                                            pass back as args.prior_items
//     issues[{ review, lens, task_id, severity, problem, evidence }], blocking[], requests[],
//     manual_steps[{ task, who, where, step, blocks_gate }], pipeline_changes[],
//     agents{ estimate{ typical, worst }, spawned, output_tokens_turn, note }, fix_rounds_used,
//     gate{ name, kind, checklist, est_minutes },
//     integrate?{ exit_criteria[CHECK], repos[{ repo: pipeline|target|records, branch, commit, pushed }],
//                 requests_applied[{ request, paths }], unowned_changes[], notes[] },  exit_missing?[], exit_failed?[],
//     checkpoint?{ checks[CHECK], repos[], unowned_changes[], notes[] } | null }
//   integrate, exit_missing and exit_failed appear when every task was done and no blocking finding was left;
//   checkpoint appears on a not-ready run unless args.checkpoint is false (null if the checkpoint agent failed).
//   CHECK = { cmd, exit_code, passed, detail }. ready is true only when every task is done, no blocking
//   finding is left, every integrate check and exit criterion was reported verbatim and passed, nothing
//   outside the owned paths changed, and each repo is committed on its stage branch (pushed, except a
//   target repo in target.mode local). A client target repo (bootstrap false, target.mode github) is pushed
//   only with the data-plane call "mp-data push"; the pipeline, records and demo repos with git push.
//   Integrate and checkpoint run git through mp-run (the operator's own gh file-stored token), except in S0 bootstrap;
//   pushes name the branch and never pass -u (mp-run keeps .git/config read-only).
//   Demo integrate and checkpoint checks always include "mp-data guard ... --history". Not ready: a checkpoint agent
//   commits the finished work as "WIP <stage> <id>: attempt N not ready" and pushes it. The integrate or checkpoint agent writes this report
//   (without integrate) to {{p.records}}/runs/<stage>.json (a change-order run: runs/S1-CO-NNN.json, so the
//   assessment report stays) and commits it with a log.md row; when no agent
//   committed (checkpoint false or failed), the orchestrator saves the returned report there itself.

export const meta = {
  name: 'run-stage',
  description: 'Run one stage of the modernization pipeline for one engagement: build, review, fix, then integrate or checkpoint',
  whenToUse: 'Once per stage per engagement, from a session rooted at the pipeline checkout. Dry run first; run the environment preflight and the prepare list; then run with prepared set to the stage id.',
  phases: [
    { title: 'Build', detail: 'stage tasks in dependency order; fan-out tasks run one agent per item' },
    { title: 'Review', detail: 'one adversarial reviewer per lens tries to refute "done"' },
    { title: 'Fix', detail: 'fix rounds for tasks with blocking findings and everything downstream of them, then re-review' },
    { title: 'Integrate', detail: 'staged guard, exit criteria, commit and push the stage branches, or a WIP checkpoint' },
  ],
}

// ---------- inputs ----------
const A = args || {}
const P = A.pipeline
if (!P || typeof P !== 'object' || !Array.isArray(P.stages)) throw new Error('args.pipeline must be the parsed pipeline.json')
const ENG = A.engagement
if (!ENG || typeof ENG !== 'object' || !ENG.id || !ENG.source || typeof ENG.source !== 'object') {
  throw new Error('args.engagement must be the parsed engagement.json, with id and source')
}
if (!P.branches || !P.branches.pipeline || !P.branches.target) throw new Error('args.pipeline.branches must name the pipeline and target branch patterns')
const RAW = P.stages.find(s => s.id === A.stage_id)
if (!RAW) throw new Error(`unknown stage "${A.stage_id}"; pipeline.json has ${P.stages.map(s => s.id).join(', ')}`)

const DRY = A.dry_run === true
const problems = []
const warnings = []
const BOOT = ENG.bootstrap === true
const MODE = BOOT ? 'bootstrap' : 'delivery'
const KIND = ENG.source.kind
const KINDS = P.source_kinds || ['accdt', 'vcs-export', 'accdb', 'mdb', 'xlsm']
const KIND_OK = KINDS.includes(KIND)
const GATE_KIND = ENG.commercial && ENG.commercial.tier === 'demo' ? 'demo' : 'client'
const CO = A.change_order == null ? null : A.change_order
const ATTEMPT = A.attempt == null ? 1 : A.attempt
if (!Number.isInteger(ATTEMPT) || ATTEMPT < 1) problems.push(`args.attempt must be a whole number from 1; got ${JSON.stringify(A.attempt)}`)
const MAX_AGENTS = A.max_agents == null ? null : A.max_agents
if (MAX_AGENTS != null && (!Number.isInteger(MAX_AGENTS) || MAX_AGENTS < 1)) problems.push(`args.max_agents must be a whole number from 1; got ${JSON.stringify(A.max_agents)}`)
const ITEMS = A.items || {}
const PRIOR = A.prior || {}
const PRIOR_ITEMS = A.prior_items || {}
const RERUN = new Set(A.rerun || [])
const SINCE = A.since || null
const GATES = A.gates || {}
const MAX_FIX_ROUNDS = RAW.max_fix_rounds == null ? 1 : RAW.max_fix_rounds

// ---------- placeholders ----------
// {{e.*}} engagement (plus derived e.db_name), {{p.*}} paths, {{s.*}} stage, {{item.*}} fan-out item.
// Scalars fill as text and arrays of scalars as "a; b" ("none" when empty); null, missing and object
// values stay unresolved, and anything left in {{x.y}} form after filling is a problem.
const PH = /\{\{((?:e|p|s|item)\.[\w.]+)\}\}/g
const ANY_PH = /\{\{[a-z_]+(?:\.\w+)+\}\}/g // anything left in our syntax after filling is a problem
function lookup(ctx, key) { return key.split('.').reduce((o, k) => (o == null ? undefined : o[k]), ctx) }
function render(v) {
  if (v == null) return undefined
  if (Array.isArray(v)) return v.every(x => x != null && typeof x !== 'object') ? (v.length ? v.join('; ') : 'none') : undefined
  if (typeof v === 'object') return undefined
  return String(v)
}
function fill(s, ctx) {
  return String(s).replace(PH, (m, k) => { const r = render(lookup(ctx, k)); return r === undefined ? m : r })
}
function deepFill(v, ctx, keep) {
  if (typeof v === 'string') return fill(v, ctx)
  if (Array.isArray(v)) return v.map(x => deepFill(x, ctx, keep))
  if (v && typeof v === 'object') {
    const o = {}
    for (const k of Object.keys(v)) o[k] = keep && keep.includes(k) ? v[k] : deepFill(v[k], ctx, keep)
    return o
  }
  return v
}
// Calls fn(path, string) for every string value whose key does not start with "_".
function walk(v, path, fn) {
  if (typeof v === 'string') fn(path, v)
  else if (Array.isArray(v)) v.forEach((x, i) => walk(x, `${path}[${i}]`, fn))
  else if (v && typeof v === 'object') for (const k of Object.keys(v)) if (!k.startsWith('_')) walk(v[k], `${path}.${k}`, fn)
}
function unresolved(v, path, ignoreItem) {
  walk(v, path, (p, s) => {
    const left = (s.match(ANY_PH) || []).filter(m => !(ignoreItem && m.startsWith('{{item.')))
    if (left.length) problems.push(`${p}: unresolved ${[...new Set(left)].join(' ')}`)
  })
}
const truthy = v => !(v == null || v === false || v === '' || (Array.isArray(v) && !v.length) || (typeof v === 'object' && !Array.isArray(v) && !Object.keys(v).length))
const isDate = v => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v)
const inside = (p, dir) => !!p && !!dir && (p === dir || p.startsWith(`${dir}/`))
const bullet = xs => (xs || []).map(x => `- ${x}`).join('\n')
const numbered = xs => (xs || []).map((x, i) => `${i + 1}. ${x}`).join('\n')

// ---------- paths ----------
const GIVEN = A.paths || {}
const PATHS = {}
for (const k of Object.keys(GIVEN)) PATHS[k] = typeof GIVEN[k] === 'string' ? GIVEN[k].replace(/\/+$/, '') : GIVEN[k]
if (!PATHS.records && typeof PATHS.pipeline === 'string') PATHS.records = `${PATHS.pipeline}/engagements/${ENG.id}`
for (const k of ['pipeline', 'target_repo', 'target', 'private', 'records', 'env_file']) {
  const v = PATHS[k]
  if (!v) problems.push(`args.paths.${k} is required`)
  else if (typeof v !== 'string' || !/^\/[\w.@+/-]*$/.test(v)) problems.push(`args.paths.${k} must be an absolute path without ~, spaces or shell characters; got ${JSON.stringify(v)}`)
}
for (const k of Object.keys(PATHS)) if (!['pipeline', 'target_repo', 'target', 'private', 'records', 'env_file'].includes(k)) warnings.push(`args.paths.${k} is not used`)
const ROOT = ENG.target && typeof ENG.target.root === 'string' ? ENG.target.root.replace(/\/+$/, '') : '.'
if (PATHS.target_repo && PATHS.target) {
  const want = ROOT === '.' || ROOT === '' ? PATHS.target_repo : `${PATHS.target_repo}/${ROOT}`
  if (PATHS.target !== want) problems.push(`args.paths.target must be ${want} (target_repo plus engagement target.root "${ROOT}"); got ${PATHS.target}`)
}
if (inside(PATHS.private, PATHS.pipeline) || inside(PATHS.private, PATHS.target_repo)) problems.push(`args.paths.private must be outside both repos; got ${PATHS.private}`)
if (inside(PATHS.records, PATHS.target_repo)) problems.push(`args.paths.records must not be inside the target repo; got ${PATHS.records}`)
const RECORDS_IN_PIPELINE = inside(PATHS.records, PATHS.pipeline)
const TARGET_REL = PATHS.target && PATHS.target !== PATHS.target_repo && inside(PATHS.target, PATHS.target_repo) ? PATHS.target.slice(PATHS.target_repo.length + 1) : ''
const RECORDS_REL = RECORDS_IN_PIPELINE && PATHS.records !== PATHS.pipeline ? PATHS.records.slice(PATHS.pipeline.length + 1) : ''

// ---------- engagement ----------
const E0 = deepFill(ENG, { p: PATHS })
const DB_NAME = ENG.db_name != null ? String(ENG.db_name) : String(ENG.id).toLowerCase().replace(/[^a-z0-9]/g, '_')
const E = { ...E0, db_name: DB_NAME }
const TARGET_MODE = E.target && E.target.mode
const CTX = { e: E, p: PATHS, s: { id: RAW.id, slug: RAW.slug, title: RAW.title, change_order: CO == null ? undefined : CO } }
const BRANCH = { pipeline: fill(P.branches.pipeline, CTX), target: fill(P.branches.target, CTX) }

if (!KIND_OK) problems.push(`engagement.source.kind must be one of ${KINDS.join(', ')}; got ${JSON.stringify(KIND)}`)
if (typeof ENG.id !== 'string' || !/^[a-z][a-z0-9]*(-[a-z0-9]+)*$/.test(ENG.id) || ENG.id.length > 40) {
  problems.push(`engagement.id must be lowercase kebab-case (^[a-z][a-z0-9]*(-[a-z0-9]+)*$, at most 40 characters); got ${JSON.stringify(ENG.id)}`)
}
const SLUG = E.app && E.app.slug
if (typeof SLUG !== 'string' || !/^[a-z][a-z0-9_]{1,29}$/.test(SLUG)) problems.push(`engagement.app.slug must match ^[a-z][a-z0-9_]{1,29}$; got ${JSON.stringify(SLUG)}`)
else if ((P.slug_reserved || []).includes(SLUG)) problems.push(`engagement.app.slug "${SLUG}" is reserved (pipeline.json slug_reserved)`)
if (!/^[a-z][a-z0-9_]{1,40}$/.test(DB_NAME)) problems.push(`engagement.db_name (or the id it defaults to) must give ^[a-z][a-z0-9_]{1,40}$; got ${JSON.stringify(DB_NAME)}`)
if (E.source.compiled_only === true) problems.push('engagement.source.compiled_only is true: only an .accde or .mde was supplied; ask the client for the source .accdb')
if (TARGET_MODE !== 'local' && TARGET_MODE !== 'github') problems.push(`engagement.target.mode must be local or github; got ${JSON.stringify(TARGET_MODE)}`)
if (TARGET_MODE === 'github' && !(E.target && E.target.repo)) problems.push('engagement.target.repo is required when target.mode is github')
else if (TARGET_MODE === 'github' && !/^[A-Za-z0-9-]+\/[A-Za-z0-9._-]+$/.test(String(E.target.repo))) {
  problems.push(`engagement.target.repo must be the GitHub owner/name (mp-data push and gh require origin https://github.com/<target.repo>); got ${JSON.stringify(E.target.repo)}`)
}
if (typeof ENG.records === 'string' && !/<[^<>]+>/.test(ENG.records) && ENG.records.replace(/\/+$/, '') !== PATHS.records) {
  problems.push(`engagement.records (${ENG.records}) differs from args.paths.records (${PATHS.records})`)
}
walk(E, 'engagement', (p, s) => { if (/<[^<>]+>/.test(s)) problems.push(`${p}: template marker left in "${s.length > 80 ? `${s.slice(0, 77)}...` : s}"`) })
unresolved(E, 'engagement', false)

// Client engagements (delivery mode): host, consent, records location, offers.
const OFFERS = P.offers || {}
const BOUGHT = (E.commercial && E.commercial.purchased) || []
let ALLOWED = null
if (!BOOT) {
  const dp = E.data_policy || {}
  if (A.host_attested !== true) problems.push('client engagement: args.host_attested must be true (run only on a host with $HOME/.config/modernization/client-host, in a session started with the client-session settings)')
  if (dp.execution !== 'local') problems.push(`client engagement: data_policy.execution must be "local"; got ${JSON.stringify(dp.execution)}`)
  if (dp.redact !== 'always') problems.push(`client engagement: data_policy.redact must be "always"; got ${JSON.stringify(dp.redact)}`)
  if (!isDate(dp.client_consent && dp.client_consent.signed_on)) problems.push('client engagement: data_policy.client_consent.signed_on must be a date (YYYY-MM-DD)')
  if (!isDate(dp.ai_terms && dp.ai_terms.checked_on)) problems.push('client engagement: data_policy.ai_terms.checked_on must be a date (YYYY-MM-DD)')
  if (!GIVEN.records) problems.push('client engagement: args.paths.records is required ($HOME/engagements/<id>/record)')
  else if (RECORDS_IN_PIPELINE) problems.push('client engagement: the records directory must not be inside the pipeline repo')
  if (!P.offers) problems.push('pipeline.json has no offers map')
  if (!Array.isArray(BOUGHT) || !BOUGHT.length) problems.push('client engagement: commercial.purchased lists no offer')
  for (const o of Array.isArray(BOUGHT) ? BOUGHT : []) {
    if (!Object.prototype.hasOwnProperty.call(OFFERS, o)) problems.push(`commercial.purchased: unknown offer ${JSON.stringify(o)}; pipeline.json offers has ${Object.keys(OFFERS).join(', ')}`)
    else if (OFFERS[o].kinds && KIND_OK && !OFFERS[o].kinds.includes(KIND)) problems.push(`offer ${o} does not apply to source kind ${KIND}`)
  }
  ALLOWED = new Set((Array.isArray(BOUGHT) ? BOUGHT : []).flatMap(o => (OFFERS[o] && (!OFFERS[o].kinds || OFFERS[o].kinds.includes(KIND)) ? OFFERS[o].stages || [] : [])))
}

// Pipeline-wide shape: the environment list is the only preflight source, and stage offers name keys of
// the top-level offers map (gating itself uses offers[*].stages).
const isOffer = k => typeof k === 'string' && Object.prototype.hasOwnProperty.call(OFFERS, k)
for (const st of P.stages) {
  if (!st || typeof st !== 'object') continue
  if (Object.prototype.hasOwnProperty.call(st, 'preflight')) problems.push(`${st.id}: remove stage preflight; environment[].stages is the source`)
  if (Object.prototype.hasOwnProperty.call(st, 'offer')) problems.push(`${st.id}: stage field offer is replaced by offers (keys of pipeline.json offers) and offer_note (text)`)
  if (st.offers !== undefined) {
    if (!Array.isArray(st.offers)) problems.push(`${st.id}.offers must be a list of keys of pipeline.json offers; got ${JSON.stringify(st.offers)}`)
    else for (const k of st.offers) if (!isOffer(k)) problems.push(`${st.id}.offers: unknown offer ${JSON.stringify(k)}; pipeline.json offers has ${Object.keys(OFFERS).join(', ') || 'nothing'}`)
  }
}
if (CO != null) {
  if (typeof CO !== 'string' || !/^CO-\d{3}$/.test(CO)) problems.push(`args.change_order must look like CO-001; got ${JSON.stringify(CO)}`)
  if (!(RAW.tasks || []).some(t => t.change_order_only)) problems.push(`${RAW.id} has no change-order task; change orders run in S1`)
}

// ---------- stage applies to this source kind? ----------
function refuse() { return new Error(`refusing to run ${RAW.id} for ${ENG.id}:\n- ${problems.join('\n- ')}`) }
if (KIND_OK && RAW.applies_to && !RAW.applies_to.includes(KIND)) {
  const reason = `${RAW.id} does not apply to source kind ${KIND}`
  log(`${reason}; nothing to do`)
  if (DRY) return { engagement: ENG.id, stage: RAW.id, dry_run: true, applies: false, not_applicable: true, ready: true, reason, problems, warnings }
  if (problems.length) throw refuse()
  return { engagement: ENG.id, stage: RAW.id, not_applicable: true, ready: true, reason }
}

// ---------- stage checks ----------
if (RAW.bootstrap_only && !BOOT) problems.push(`${RAW.id} runs in bootstrap mode only`)
if (!BOOT) {
  if (!ALLOWED.has(RAW.id)) problems.push(`${RAW.id} is not a stage of the purchased offers (${(Array.isArray(BOUGHT) ? BOUGHT : []).join(', ') || 'none'})`)
  // A local target repo is never pushed: only stages of an offer sold in target mode local (health-check,
  // assessment) may run while target.mode is local; code-delivering stages need the client's GitHub repo.
  if (TARGET_MODE === 'local' && !(Array.isArray(BOUGHT) ? BOUGHT : []).some(o => OFFERS[o] && OFFERS[o].target_mode === 'local' && (OFFERS[o].stages || []).includes(RAW.id))) problems.push(`${RAW.id} delivers code to the client's repo: switch target.mode to github first (docs/runbook.md 1.6)`)
  if (RAW.id === 'S3' && !(E.database && E.database.stage1)) problems.push('client S3: set database.stage1 (freeze_window, fresh_copy, relink_by, rollback, office_bitness, odbc_driver) in engagement.json first')
  for (const pre of (P.stage_prerequisites || {})[RAW.id] || []) {
    if (ALLOWED.has(pre) && !(GATES[pre] && GATES[pre].approved)) problems.push(`gate ${pre} is not approved in gates.json (args.gates.${pre}.approved)`)
  }
  if (RAW.id === 'S2' && !(GATES.S1 && GATES.S1.deposit_received === true)) problems.push('S2 needs the S1 deposit: gates.json S1.deposit_received is not true')
  if (['S2', 'S3'].includes(RAW.id) && KIND === 'accdt' && !E.source.data_copy && !E.source.saved_with_data) {
    problems.push(`design-only .accdt: set source.data_copy (or saved_with_data) before ${RAW.id}`)
  }
  if (CO != null && !(GATES.S1 && GATES.S1.approved)) problems.push('a change order amends the signed spec, but gates.json S1 is not approved')
}

const S = deepFill({ id: RAW.id, slug: RAW.slug, title: RAW.title, commit_message: RAW.commit_message, agent_budget: RAW.agent_budget }, CTX)
// The run report: a change-order run gets its own file, so it never overwrites the assessment report.
const RUNS_FILE = `${S.id}${CO ? `-${CO}` : ''}.json`
if (!S.commit_message) problems.push(`${RAW.id}: commit_message is empty`)
unresolved({ commit_message: S.commit_message, branches: BRANCH }, RAW.id, false)
const CONSTRAINTS = (P.constraints || []).map(c => fill(c, CTX))
unresolved(CONSTRAINTS, 'pipeline.constraints', false)
const ZONES = deepFill((P.data_plane && P.data_plane.zones) || {}, CTX)
const WRAPPER = (P.data_plane && P.data_plane.wrapper) || 'mp-data'
unresolved(ZONES, 'pipeline.data_plane.zones', false)

// Anchoring (see the header). Commands are filled first, then anchored per agent slot.
const RUNNER = (P.data_plane && P.data_plane.runner) || 'mp-run'
const ENG_FILE = `${PATHS.records}/engagement.json`
const BRACED = BOOT && RAW.id === 'S0' // mp-run is built by S0.workspace, so an S0 bootstrap run cannot use it
const escRe = s => s.replace(/[.*+?^$()|[\]\\{}]/g, '\\$&')
function hash36(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0 } return h.toString(36).padStart(7, '0').slice(-6) }
function slotFor(label) {
  const s = String(label).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  return s.length <= 24 ? s : `${s.slice(0, 17)}_${hash36(s)}`
}
// Data-plane calls. Placeholders left in a command count as plain words; single-quoted text is literal.
const SUFFIX = /\$MP_DB_SUFFIX(?![A-Za-z0-9_])/g
const bareOf = c => String(c).replace(/\{\{[^{}]*\}\}/g, 'PH').replace(/'[^']*'/g, "''")
const SHELL = /&&|\|\||[;|&<>`(){}\n'"$]|(^|\s)(cd|if|then|else|fi|for|while|until|do|done|case)(\s|$)/
function isDataPlane(cmd) {
  const c = String(cmd).trim()
  return c.startsWith(`${WRAPPER} `) && !SHELL.test(bareOf(c).replace(SUFFIX, 'SLOT').replace(/''/g, 'Q').replace(/"[^"$`\\]*"/g, 'Q'))
}
// mp-data as a command word: at the start, after an operator, then/do/else, ( { $( or a backtick, or behind
// env assignments. Mentions as text (grep patterns, jq strings, command -v mp-data, scripts/mp-data) are fine.
const INVOKES = new RegExp(`(^|&&|\\|\\||[;|&({\`]|\\$\\(|\\b(?:then|do|else)\\b)\\s*(?:env\\s+)?(?:[A-Za-z_]\\w*=\\S*\\s+)*${escRe(WRAPPER)}(?=\\s|;|$)`)
function anchor(cmd, slot) {
  const c = String(cmd).trim()
  if (isDataPlane(c)) return `${WRAPPER} --slot ${slot} ${c.slice(WRAPPER.length).trim().replace(SUFFIX, slot)}`
  if (BRACED) {
    const head = `. ${PATHS.env_file} && export MP_DB_SUFFIX=${slot} && `
    return /^cd\s/.test(c) ? `${head}{ ${c}; }` : `${head}cd ${PATHS.pipeline} && { ${c}; }`
  }
  return `${RUNNER} --eng ${ENG_FILE} --slot ${slot} -- '${c.replace(/'/g, "'\\''")}'`
}
const short = c => (c.length > 160 ? `${c.slice(0, 157)}...` : c)
function pure(cmds, where) {
  for (const c of cmds || []) {
    if (typeof c !== 'string') continue
    if (isDataPlane(c)) {
      if (new RegExp(`^${escRe(WRAPPER)}(\\s+--[\\w-]+)*\\s+--slot(\\s|=|$)`).test(c.trim())) problems.push(`${where}: a data-plane call must not pass --slot (the driver adds it): ${short(c)}`)
    } else if (INVOKES.test(bareOf(c))) problems.push(`${where}: ${WRAPPER} must be a standalone data-plane call: ${short(c)}`)
  }
}
function checkCmds(expected, reported) {
  const norm = c => String(c).replace(/\s+/g, ' ').trim()
  const got = new Map((reported || []).filter(c => c && typeof c.cmd === 'string').map(c => [norm(c.cmd), c]))
  const missing = expected.filter(c => !got.has(norm(c)))
  const failed = expected.filter(c => got.has(norm(c)) && !(got.get(norm(c)).passed === true && got.get(norm(c)).exit_code === 0))
  return { missing, failed }
}
// Agent commands must be one line: the client Bash hook blocks any newline, even inside quotes.
// Environment commands (env true) are never anchored and run outside Claude for clients, so they may
// span lines; like every pipeline.json command they call mp-data only as a standalone data-plane call.
function noBang(cmds, where, env) {
  for (const c of cmds || []) {
    if (typeof c !== 'string') continue
    if (c.trim().startsWith('!')) problems.push(`${where}: command starts with "!" (write a positive check or "cmd; test $? -eq 1"): ${c}`)
    if (!env && /[\r\n]/.test(c.trim())) problems.push(`${where}: command spans more than one line (the client Bash hook blocks newlines): ${short(c)}`)
  }
  pure(cmds, where)
}
// The dry run's items_from command: the fan-out's jq program on its file, before anchoring (null when
// items_from lacks a file or a jq program).
function itemsFromCmd(t) {
  const x = t.items_from || {}
  if (typeof x.file !== 'string' || !x.file || typeof x.jq !== 'string' || !x.jq) return null
  return `jq -c '${x.jq.replace(/'/g, "'\\''")}' ${x.file}`
}

// Exit criteria, prepare list, gate.
const EXIT_RAW = (RAW.exit_criteria || []).map(c => fill(c, CTX))
// Client S0: through mp-run the data zone must read as empty (proves the isolation before any agent work).
if (RAW.id === 'S0' && !BOOT) EXIT_RAW.push(fill('test "$(cat {{p.private}}/sources/* 2>/dev/null | wc -c)" = 0', CTX))
noBang(EXIT_RAW, `${RAW.id}.exit_criteria`)
unresolved(EXIT_RAW, `${RAW.id}.exit_criteria`, false)
const PREPARE_SLOT = slotFor(`${RAW.id}:prepare`)
const PREPARE_RAW = (RAW.prepare || []).map(c => fill(c, CTX))
noBang(PREPARE_RAW, `${RAW.id}.prepare`)
unresolved(PREPARE_RAW, `${RAW.id}.prepare`, false)
const PREPARE = PREPARE_RAW.map(c => anchor(c, PREPARE_SLOT))
const G0 = RAW.gate || {}
let checklist = G0[GATE_KIND] || G0.client || []
if (GATE_KIND === 'demo' && checklist.length === 1 && /^same as for a client/i.test(checklist[0])) checklist = G0.client || []
const GATE = { name: fill(G0.name || RAW.title, CTX), kind: GATE_KIND, checklist: checklist.map(c => fill(c, CTX)), est_minutes: G0.est_minutes == null ? null : G0.est_minutes }
unresolved(GATE, `${RAW.id}.gate`, false)

// Environment preflight, derived from pipeline.json environment alone (stages have no preflight lists):
// an item applies when its stages include this stage and its kinds (if any) include the source kind.
// Its when (if any) is a shell condition the orchestrator runs first; the item is skipped when it fails.
const ENV_ITEMS = Array.isArray(P.environment) ? P.environment.filter(x => x && typeof x === 'object') : []
if (!Array.isArray(P.environment)) problems.push('pipeline.json has no environment list')
const STAGE_IDS = P.stages.map(s => s && s.id)
const ENV_SEEN = new Set()
for (const x of ENV_ITEMS) {
  const id = x.id || '(no id)'
  if (ENV_SEEN.has(id)) problems.push(`environment: duplicate item id ${id}`)
  ENV_SEEN.add(id)
  if (!Array.isArray(x.stages) || !x.stages.length) problems.push(`environment.${id}: stages must list the stages the item applies to`)
  else for (const s of x.stages) if (!STAGE_IDS.includes(s)) problems.push(`environment.${id}: unknown stage ${JSON.stringify(s)}`)
  if (x.kinds !== undefined && (!Array.isArray(x.kinds) || x.kinds.some(k => !KINDS.includes(k)))) problems.push(`environment.${id}: kinds must be a list of source kinds (${KINDS.join(', ')})`)
  if (!x.check) problems.push(`environment.${id}: check is empty`)
}
const ENVIRONMENT = ENV_ITEMS
  .filter(x => x.id && Array.isArray(x.stages) && x.stages.includes(RAW.id) && (!Array.isArray(x.kinds) || x.kinds.includes(KIND)))
  .map(x => {
    const o = { id: x.id, check: fill(x.check || '', CTX) }
    for (const k of ['fix', 'when']) if (x[k]) o[k] = fill(x[k], CTX)
    if (x.optional) o.optional = true
    if (x.writes) o.writes = x.writes
    if (x.note) o.note = fill(x.note, CTX)
    noBang([o.check, o.fix, o.when], `environment.${x.id}`, true)
    return o // never anchored: these commands need Docker and data.env
  })
unresolved(ENVIRONMENT.map(({ id, check, fix, when }) => ({ id, check, fix, when })), 'environment', false)
const PREFLIGHT = ENVIRONMENT.map(x => x.id)

// ---------- tasks ----------
const RAW_TASKS = RAW.tasks || []
const BY_ID = new Map()
for (const t of RAW_TASKS) {
  if (!t || !t.id) { problems.push(`${RAW.id}: a task has no id`); continue }
  if (BY_ID.has(t.id)) problems.push(`${RAW.id}: duplicate task id ${t.id}`)
  BY_ID.set(t.id, t)
  if (!['build', 'fanout', 'review'].includes(t.kind)) problems.push(`${t.id}: kind must be build, fanout or review; got ${JSON.stringify(t.kind)}`)
  for (const d of t.depends_on || []) if (!RAW_TASKS.some(x => x.id === d)) problems.push(`${t.id} depends on unknown task ${d}`)
}
function reasonOf(t) {
  if (t.applies_to && !t.applies_to.includes(KIND)) return `source kind ${KIND} is not in applies_to`
  if (t.bootstrap_only && !BOOT) return 'bootstrap only'
  if (t.kind !== 'review' && !!t.change_order_only !== (CO != null)) return CO != null ? 'not part of a change-order run' : 'change-order runs only'
  if (t.skip_if && truthy(lookup(E, t.skip_if))) return `skip_if: engagement ${t.skip_if} is set`
  return null
}
const TASKS = new Map()
for (const t of RAW_TASKS) {
  if (!t || !t.id || reasonOf(t)) continue
  const f = deepFill(t, CTX, ['each'])
  f.depends_on = (t.depends_on || []).filter(d => BY_ID.has(d) && !reasonOf(BY_ID.get(d)))
  if (t.kind === 'review') f.lenses = (t.lenses_by_kind && t.lenses_by_kind[KIND]) ? deepFill(t.lenses_by_kind[KIND], CTX) : f.lenses
  TASKS.set(t.id, f)
}
const WORK = [...TASKS.values()]
const REVIEWS = WORK.filter(t => t.kind === 'review')
const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']
const OWN_ROOTS = ['pipeline', 'target', 'target-repo', 'private', 'records']
// A fan-out item is its each template, plus the fan-out's own stack_keys, effort, reuse and bootstrap.
function fillEach(t, item) {
  const f = deepFill(t.each, item ? { ...CTX, item } : CTX)
  return { ...f, kind: 'build', fanout: t.id, stack_keys: f.stack_keys || t.stack_keys, effort: f.effort || t.effort, reuse: f.reuse || t.reuse, bootstrap: f.bootstrap || t.bootstrap }
}
function checkSpec(x, where) {
  if (!Array.isArray(x.owns) || !x.owns.length) problems.push(`${where}: owns is empty`)
  for (const o of [...(x.owns || []), ...(x.owns_except || [])]) {
    const root = String(o).split(':')[0]
    if (!String(o).includes(':') || !OWN_ROOTS.includes(root)) problems.push(`${where}: owned path "${o}" must start with one of ${OWN_ROOTS.map(r => `${r}:`).join(', ')}`)
    if (/(^|[:/])\.\.(\/|$)/.test(o)) problems.push(`${where}: owned path "${o}" contains ..`)
  }
  if (!Array.isArray(x.acceptance) || !x.acceptance.length) problems.push(`${where}: acceptance is empty`)
  if (!x.goal) problems.push(`${where}: goal is empty`)
  if (x.effort && !EFFORTS.includes(x.effort)) problems.push(`${where}: effort must be one of ${EFFORTS.join(', ')}`)
  for (const k of x.stack_keys || []) if (!P.stack || !(k in P.stack)) problems.push(`${where}: stack key ${k} is not in pipeline.json stack`)
  noBang(x.acceptance, `${where}.acceptance`)
  noBang(x.verify, `${where}.verify`)
  const writes = (x.acceptance || []).some(c => /--out\s|--report\s|--into\s|--replace|\brecord\b/.test(c) && !/--check\b|--verify\b/.test(c))
  if (writes && !(x.verify && x.verify.length)) warnings.push(`${where}: an acceptance command writes, but there is no verify list for reviewers`)
}
for (const t of WORK) {
  if (t.kind === 'build') { checkSpec(t, t.id); unresolved(t, t.id, false) }
  if (t.kind === 'fanout') {
    unresolved({ ...t, each: undefined }, t.id, false)
    if (t.items_from) {
      const cmd = itemsFromCmd(t)
      if (cmd) noBang([cmd], `${t.id}.items_from`)
      else problems.push(`${t.id}.items_from must have a file and a jq program (strings)`)
    }
    if (!t.each || typeof t.each !== 'object') { problems.push(`${t.id}: fan-out has no each template`); continue }
    if (!String(t.each.id || '').includes('{{item.id}}')) problems.push(`${t.id}: each.id must contain {{item.id}}`)
    for (const o of t.each.owns || []) if (!String(o).includes('{{item.id}}')) problems.push(`${t.id}: each.owns "${o}" lacks {{item.id}}, so items would collide`)
    const f = fillEach(t, null)
    checkSpec(f, `${t.id}.each`)
    unresolved(f, `${t.id}.each`, true)
    if (!t.scout && !t.items_from) warnings.push(`${t.id}: no scout and no items_from; the run needs args.items`)
  }
  if (t.kind === 'review') {
    if (!Array.isArray(t.lenses) || !t.lenses.length) problems.push(`${t.id}: no lenses (lenses or lenses_by_kind.${KIND})`)
    if (!t.depends_on.length) problems.push(`${t.id}: reviews nothing for source kind ${KIND} (no applicable dependency)`)
    if (t.effort && !EFFORTS.includes(t.effort)) problems.push(`${t.id}: effort must be one of ${EFFORTS.join(', ')}`)
    unresolved({ ...t, lenses_by_kind: undefined }, t.id, false)
  }
}
const RAW_IDS = new Set(RAW_TASKS.map(t => t && t.id))
const itemIdRe = t => new RegExp(`^${String(t.each.id).split('{{item.id}}').map(escRe).join('[a-z][a-z0-9_]*')}$`)
for (const id of RERUN) {
  const itemLike = RAW_TASKS.some(t => t && t.kind === 'fanout' && t.each && t.each.id && itemIdRe(t).test(id))
  if (!RAW_IDS.has(id) && !itemLike) problems.push(`args.rerun: ${id} is not a task or fan-out item of ${RAW.id}`)
}
for (const k of Object.keys(ITEMS)) if (!(TASKS.get(k) && TASKS.get(k).kind === 'fanout')) warnings.push(`args.items.${k}: no applicable fan-out task with that id`)

function ancestors(id, out = new Set()) {
  for (const d of (TASKS.get(id) || { depends_on: [] }).depends_on) if (!out.has(d)) { out.add(d); ancestors(d, out) }
  return out
}

// Fan-out items.
const ITEM_PATTERN = new RegExp((P.item_ids && P.item_ids.pattern) || '^[a-z][a-z0-9_]{1,29}$')
const ITEM_RESERVED = new Set((P.item_ids && P.item_ids.reserved) || ['users', 'contrib', 'static', 'templates', 'utils', 'legacy', 'core', 'domain', 'tests', 'e2e', 'rules', 'config', 'admin', 'auth', 'sessions'])
const PY_KEYWORDS = new Set(['and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield'])
function knownItems(id) { return ITEMS[id] || PRIOR_ITEMS[id] || null }
function itemProblem(t, items) {
  if (!Array.isArray(items) || !items.length) return 'no items'
  const ids = items.map(i => i && i.id)
  const malformed = items.filter(i => !i || typeof i.id !== 'string' || typeof i.title !== 'string' || typeof i.scope !== 'string')
  if (malformed.length) return 'every item needs string id, title and scope'
  const bad = ids.filter(id => !ITEM_PATTERN.test(id) || PY_KEYWORDS.has(id))
  if (bad.length) return `item ids not matching ${ITEM_PATTERN.source} (or Python keywords): ${bad.join(', ')}`
  const clash = ids.filter(id => ITEM_RESERVED.has(id) || id === SLUG)
  if (clash.length) return `item ids clash with reserved names or the app slug: ${clash.join(', ')}`
  const dup = ids.filter((id, i) => ids.indexOf(id) !== i)
  if (dup.length) return `duplicate item ids: ${[...new Set(dup)].join(', ')}`
  if (t.max_items && items.length > t.max_items && A.allow_over_budget !== true) return `${items.length} items exceed max_items=${t.max_items}; merge areas or pass allow_over_budget`
  return null
}
for (const t of WORK) if (t.kind === 'fanout' && knownItems(t.id)) {
  const bad = itemProblem(t, knownItems(t.id))
  if (bad) { if (DRY) problems.push(`${t.id}: ${bad}`) } else knownItems(t.id).forEach(it => unresolved(fillEach(t, it), `${t.id}[${it.id}]`, false))
}

// Owned paths of tasks that can run at the same time must not overlap (absolute paths, so target: and
// target-repo: meet when target.root is "." and records: meets pipeline: for demos; owns_except honoured).
const BASE = { pipeline: PATHS.pipeline, target: PATHS.target, 'target-repo': PATHS.target_repo, private: PATHS.private, records: PATHS.records }
function absOwned(o) {
  const i = String(o).indexOf(':')
  const base = i > 0 ? BASE[o.slice(0, i)] : null
  return base ? `${base}/${o.slice(i + 1)}` : null
}
function glob(s) { return new RegExp(`^${s.replace(/[.+^$()|[\]\\{}?]/g, '\\$&').replace(/\*/g, '[^/]*')}${s.endsWith('/') ? '.*' : ''}$`) }
const overlap = (a, b) => a === b || glob(a).test(b) || glob(b).test(a)
function unitsOf(t) {
  if (t.kind === 'build') return [{ task: t.id, id: t.id, owns: t.owns || [], except: t.owns_except || [] }]
  if (t.kind !== 'fanout' || !t.each) return []
  const its = knownItems(t.id)
  if (its && !itemProblem(t, its)) return its.map(it => { const f = fillEach(t, it); return { task: t.id, id: f.id, owns: f.owns || [], except: f.owns_except || [] } })
  const f = fillEach(t, null)
  const ph = s => String(s).replace(/\{\{item\.id\}\}/g, '<item>')
  return [{ task: t.id, id: ph(f.id), owns: (f.owns || []).map(ph), except: (f.owns_except || []).map(ph) }]
}
const UNITS = WORK.flatMap(unitsOf).map(u => ({ ...u, abs: u.owns.map(absOwned).filter(Boolean), exAbs: u.except.map(absOwned).filter(Boolean) }))
const excluded = (u, p) => u.exAbs.some(e => glob(e).test(p))
for (let i = 0; i < UNITS.length; i++) {
  for (let j = i + 1; j < UNITS.length; j++) {
    const a = UNITS[i]; const b = UNITS[j]
    const ordered = a.task !== b.task && (ancestors(a.task).has(b.task) || ancestors(b.task).has(a.task))
    for (const x of a.abs) for (const y of b.abs) {
      if (!overlap(x, y) || excluded(a, y) || excluded(b, x)) continue
      if (ordered) warnings.push(`nested ownership across ordered tasks: ${a.id} owns ${x}, ${b.id} owns ${y}`)
      else problems.push(`${a.id} and ${b.id} can run at the same time and both own ${x} / ${y}`)
    }
  }
}

// Prior reuse (new session): a task whose prior result is done, that is not rerun and whose upstream is all reused.
const priorDone = id => !!(PRIOR[id] && PRIOR[id].status === 'done')
const rerunOf = (id, fan) => RERUN.has(id) || (fan != null && RERUN.has(fan))
const PREDICT = new Map()
function predicted(id) {
  if (PREDICT.has(id)) return PREDICT.get(id)
  const t = TASKS.get(id)
  const up = t.depends_on.every(predicted)
  let v = false
  if (t.kind === 'build') v = up && priorDone(id) && !rerunOf(id)
  if (t.kind === 'fanout') {
    const its = knownItems(id)
    v = up && !!its && !itemProblem(t, its) && its.every(it => { const iid = fillEach(t, it).id; return priorDone(iid) && !rerunOf(iid, id) })
  }
  PREDICT.set(id, v)
  return v
}

function estimate() {
  let typical = 1 // integrate
  let worst = 2 // integrate, plus a checkpoint when it is not ready
  for (const t of WORK) {
    if (t.kind === 'build') { if (!predicted(t.id)) typical += 1; worst += (predicted(t.id) ? 0 : 1) + MAX_FIX_ROUNDS }
    if (t.kind === 'fanout') {
      const its = knownItems(t.id)
      const usable = its && !itemProblem(t, its)
      const n = usable ? its.length : (t.max_items || 4)
      const fresh = usable ? its.filter(it => { const iid = fillEach(t, it).id; return !(predicted(t.id) || (priorDone(iid) && !rerunOf(iid, t.id) && t.depends_on.every(predicted))) }).length : n
      const scout = its ? 0 : (t.scout ? 1 : 0)
      typical += scout + fresh
      worst += scout + fresh + n * MAX_FIX_ROUNDS
    }
    if (t.kind === 'review') { const n = (t.lenses || []).length || 1; typical += n; worst += n * (1 + MAX_FIX_ROUNDS) }
  }
  return { typical, worst }
}
const EST = estimate()
if (S.agent_budget != null && EST.typical > S.agent_budget) warnings.push(`typical agent count ${EST.typical} is above the stage agent_budget ${S.agent_budget}`)

if (!DRY && A.prepared !== RAW.id) problems.push(`run the dry run's environment preflight and prepare list, then pass args.prepared = "${RAW.id}"`)

// ---------- dry run ----------
const PREV = (() => { const i = P.stages.indexOf(RAW); for (let j = i - 1; j >= 0; j--) if (!P.stages[j].applies_to || P.stages[j].applies_to.includes(KIND)) return P.stages[j].id; return null })()
const ISLOT = slotFor(`${RAW.id}:integrate`)
const EXIT = EXIT_RAW.map(c => anchor(c, ISLOT))
log(`${ENG.id} ${RAW.id} (${MODE}${CO ? ` ${CO}` : ''}, source ${KIND}, attempt ${ATTEMPT}): ${WORK.length} tasks, about ${EST.typical} agents (worst case ${EST.worst}); stage agent_budget ${S.agent_budget == null ? 'unset' : S.agent_budget}`)
const NOT_APPLICABLE = RAW_TASKS.filter(t => t && t.id && reasonOf(t)).map(t => t.id)
if (NOT_APPLICABLE.length) log(`not applicable: ${NOT_APPLICABLE.join(', ')}`)
if (DRY) {
  const itemsFrom = {}
  for (const t of WORK) {
    if (t.kind === 'fanout' && t.items_from) {
      const jq = itemsFromCmd(t)
      itemsFrom[t.id] = { file: t.items_from.file, jq: t.items_from.jq, cmd: jq ? anchor(jq, PREPARE_SLOT) : null }
    }
  }
  const items = {}
  for (const t of WORK) if (t.kind === 'fanout' && knownItems(t.id)) items[t.id] = knownItems(t.id)
  return {
    engagement: ENG.id, stage: RAW.id, dry_run: true, applies: true, mode: MODE, attempt: ATTEMPT, change_order: CO, branches: BRANCH,
    problems, warnings,
    tasks: RAW_TASKS.filter(t => t && t.id).map(t => {
      const f = TASKS.get(t.id)
      const owns = t.kind === 'fanout' ? (t.each && t.each.owns) || [] : t.owns || []
      return f
        ? { id: t.id, kind: t.kind, applies: true, depends_on: f.depends_on, owns: deepFill(owns, CTX), reused: t.kind !== 'review' && predicted(t.id) }
        : { id: t.id, kind: t.kind, applies: false, reason: reasonOf(t), depends_on: t.depends_on || [], owns: deepFill(owns, CTX), reused: false }
    }),
    tasks_not_applicable: NOT_APPLICABLE,
    estimate: EST, agent_budget: S.agent_budget == null ? null : S.agent_budget, over_budget: S.agent_budget != null && EST.typical > S.agent_budget,
    preflight: PREFLIGHT, environment: ENVIRONMENT, prepare: PREPARE, exit_criteria: EXIT,
    items_from: itemsFrom, items, gate: GATE,
  }
}
if (problems.length) throw refuse()

// ---------- schemas ----------
const checkSchema = cmds => ({
  type: 'object',
  properties: {
    // A task may run the same command twice (S4.models rebuilds the schema after migrate), but enum items must be unique.
    cmd: cmds && cmds.length ? { type: 'string', enum: [...new Set(cmds)] } : { type: 'string' },
    exit_code: { type: 'integer' },
    passed: { type: 'boolean' },
    detail: { type: 'string' },
  },
  required: ['cmd', 'exit_code', 'passed'],
})
const STRINGS = { type: 'array', items: { type: 'string' } }
const MANUAL = {
  type: 'object',
  properties: {
    who: { type: 'string', enum: ['operator', 'client', 'msp'] },
    where: { type: 'string', enum: ['windows-box', 'client-tenant', 'github', 'hosting', 'any'] },
    step: { type: 'string' },
    blocks_gate: { type: 'boolean' },
  },
  required: ['who', 'where', 'step', 'blocks_gate'],
}
const resultSchema = cmds => ({
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['done', 'blocked'] },
    summary: { type: 'string' },
    files_changed: STRINGS,
    acceptance: { type: 'array', items: checkSchema(cmds) },
    pipeline_changes: STRINGS,
    requests: STRINGS,
    manual_steps: { type: 'array', items: MANUAL },
    notes: STRINGS,
  },
  required: ['status', 'summary', 'files_changed', 'acceptance'],
})
const scoutSchema = () => ({
  type: 'object',
  properties: {
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: { id: { type: 'string', pattern: ITEM_PATTERN.source }, title: { type: 'string' }, scope: { type: 'string' } },
        required: ['id', 'title', 'scope'],
      },
    },
    uncovered: STRINGS,
  },
  required: ['items', 'uncovered'],
})
const verdictSchema = ids => ({
  type: 'object',
  properties: {
    issues: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          task_id: { type: 'string', enum: ids },
          severity: { type: 'string', enum: ['blocking', 'minor'] },
          problem: { type: 'string' },
          evidence: { type: 'string' },
        },
        required: ['task_id', 'severity', 'problem', 'evidence'],
      },
    },
  },
  required: ['issues'],
})
const REPO = {
  type: 'object',
  properties: {
    repo: { type: 'string', enum: ['pipeline', 'target', 'records'] },
    branch: { type: 'string' },
    commit: { type: 'string' },
    pushed: { type: 'boolean' },
  },
  required: ['repo', 'branch', 'commit', 'pushed'],
}
const integratedSchema = cmds => ({
  type: 'object',
  properties: {
    exit_criteria: { type: 'array', items: checkSchema(cmds) },
    repos: { type: 'array', items: REPO },
    requests_applied: { type: 'array', items: { type: 'object', properties: { request: { type: 'string' }, paths: STRINGS }, required: ['request', 'paths'] } },
    unowned_changes: STRINGS,
    notes: STRINGS,
  },
  required: ['exit_criteria', 'repos', 'unowned_changes'],
})
const checkpointSchema = cmds => ({
  type: 'object',
  properties: { checks: { type: 'array', items: checkSchema(cmds) }, repos: { type: 'array', items: REPO }, unowned_changes: STRINGS, notes: STRINGS },
  required: ['checks', 'repos', 'unowned_changes'],
})

// ---------- prompts ----------
let SPAWNED = 0
function spawn(prompt, o, exempt) {
  if (!exempt && MAX_AGENTS != null && SPAWNED >= MAX_AGENTS) throw new Error(`agent cap reached: args.max_agents is ${MAX_AGENTS}`)
  SPAWNED += 1
  return agent(prompt, o)
}
function opts(label, phaseTitle, schema, effort) {
  const o = { label, phase: phaseTitle, schema }
  if (effort) o.effort = effort
  return o
}
const list = v => (Array.isArray(v) ? (v.length ? v.join('; ') : 'none') : (v == null ? 'none' : String(v)))
function preamble() {
  const db = E.database || {}
  const hosting = E.hosting && typeof E.hosting === 'object' ? `${E.hosting.provider || 'none'}, managed by ${E.hosting.managed_by || 'nobody'}` : String(E.hosting || 'none')
  const dp = E.data_policy || {}
  const scope = E.scope || {}
  return [
    `Engagement ${E.id}${GATE_KIND === 'demo' && E.client ? ` (${E.client})` : ''}. Stage ${S.id}: ${S.title}${CO ? `, change order ${CO}` : ''}. Attempt ${ATTEMPT}.`,
    `Source: ${KIND} at ${E.source.path} (data zone: only ${WRAPPER} reads it).${KIND === 'accdb' || KIND === 'mdb' ? ' Forms, reports and VBA are not available for this source: mark rules that would come from forms or VBA as not assessed.' : ''}`,
    BOOT
      ? 'Mode: BOOTSTRAP. The pipeline tools are being built on this engagement. Build what your build spec says inside the pipeline repo, with tests, then apply it to this engagement. If a tool already exists, reuse it and add only what this engagement is missing.'
      : 'Mode: DELIVERY. Use the pipeline\'s existing tools. If a tool cannot handle this client\'s app, extend it in the pipeline repo with tests and list the extension in pipeline_changes; never work around a tool gap inside the target repo.',
    `Engagement facts: database ${db.primary || 'unset'}${(db.verify_also || []).length ? ` (also verified on ${db.verify_also.join(', ')})` : ''}, database name ${E.db_name}; hosting ${hosting}; sign-in ${(E.auth && E.auth.mode) || 'local'}; target mode ${TARGET_MODE}${TARGET_MODE === 'local' ? ' (the target repo is committed, never pushed)' : ''}; data policy: redact ${dp.redact || 'unset'}, execution ${dp.execution || 'unset'}, scrub_design ${dp.scrub_design === true}.`,
    `Scope: areas hint ${list(scope.areas_hint)}. Must keep: ${list(scope.must_keep)}. Foundation (built in S4, not an area): ${list(scope.foundation)}.`,
    `Pipeline repo (tools, templates, contracts): ${PATHS.pipeline}, branch ${BRANCH.pipeline}. Relative paths in inputs and reuse lists are inside it.`,
    `Target repo (what the client receives): ${PATHS.target_repo}, branch ${BRANCH.target}; this engagement's root in it is ${PATHS.target}.`,
    `Records: ${PATHS.records} (engagement.json, answers.md, log.md, runs/). Private directory: ${PATHS.private} (never commit it).`,
    'Owned paths are written pipeline:<path> (pipeline repo), target:<path> (engagement root), target-repo:<path> (target repo root), records:<file> (records directory) or private:<path> (private directory).',
    `Data zones: you may read the design zone (${list(ZONES.design)}). Never open, list, cat, query or copy anything in the data zone (${list(ZONES.data)}); only ${WRAPPER} commands touch it.`,
    `Client answers: ${PATHS.records}/answers.md, if present; it overrides assumptions recorded in notes.${PREV ? ` Previous stage handoff: ${PATHS.records}/runs/${PREV}.json, if present (read its notes, requests and manual_steps).` : ''}`,
    RUN_RULE,
    LONG_RULE,
    CLIENT_RULE,
    `Rules for every task:\n${numbered(CONSTRAINTS)}`,
  ].filter(Boolean).join('\n')
}
// A checkpoint's --history guard ran past the Bash tool's 120 s default and was reported as not run.
const LONG_RULE = 'Long commands (mp-data guard --history, equivalence, golden record, test suites) can take many minutes: run each with the Bash tool timeout set to 600000 ms. If one is still moved to the background, wait for it to exit and use its exit code; never report a check as not run because it was slow.'
// How commands run (see the header); client sessions also have a Bash hook that allows only mp-run and mp-data.
const RUN_RULE = BRACED
  ? `Your shell resets between calls, so run every command exactly as written and report it verbatim. In this S0 bootstrap run (${RUNNER} does not exist yet) a command is anchored as \`. ${PATHS.env_file} && export MP_DB_SUFFIX=<your slot> && cd ${PATHS.pipeline} && { <command>; }\`, and a data-plane call runs unwrapped as \`${WRAPPER} --slot <your slot> <command> ...\`.`
  : `Your shell resets between calls, so run every command exactly as written and report it verbatim. A data-plane call runs unwrapped as \`${WRAPPER} --slot <your slot> <command> ...\`. Every other command runs as \`${RUNNER} --eng ${ENG_FILE} --slot <your slot> -- '<command>'\` (inside the quotes each ' is written '\\''): it sources the env file, sets MP_DB_SUFFIX to your slot, starts in the pipeline checkout unless the command starts with cd, and hides the data zone (its directories read as empty), other engagements, data.env, the pass store, Docker and $XDG_RUNTIME_DIR (keyring, ssh-agent). Inside it the installed tools, the Claude settings, .git/hooks and .git/config are read-only. Work that needs the data zone goes through ${WRAPPER}.`
const CLIENT_RULE = BOOT
  ? ''
  : `This session blocks any Bash call that is not one ${RUNNER} or ${WRAPPER} call. Run ad-hoc commands as \`${RUNNER} --eng ${ENG_FILE} --slot <your slot> -- '<command>'\`. The Read, Grep and Glob tools work except on denied paths.`
function stackSlice(keys) {
  if (!keys || !keys.length || !P.stack) return ''
  return `Pinned stack (pipeline.json stack):\n${bullet(keys.map(k => `${k}: ${JSON.stringify(deepFill(P.stack[k], CTX))}`))}`
}
function attemptText(id, fan) {
  const prev = PRIOR[id]
  if (!(rerunOf(id, fan) || (prev && prev.status !== 'done'))) return ''
  return `Attempt ${ATTEMPT}. Previous result: ${prev ? `${prev.status}: ${prev.summary}` : 'none'}. What changed: ${SINCE || `see ${PATHS.records}/answers.md`}. Files from that attempt may already be in your owned paths; check them and continue.`
}
function taskPrompt(t, upstream, findings, acc, ver, slot) {
  const boot = BOOT && t.bootstrap
  return [
    preamble(),
    `Your task: ${t.id}: ${t.title}`,
    `Goal: ${t.goal}`,
    `Paths you own (create or edit only these): ${t.owns.join(', ')}`,
    t.owns_except && t.owns_except.length ? `Never edit (inside your paths but owned by other tasks): ${t.owns_except.join(', ')}` : '',
    `Your MP_DB_SUFFIX slot: ${slot}. Your Django test database is test_${E.db_name}_${slot}; never use, drop or recreate another agent's database.`,
    stackSlice(t.stack_keys),
    t.inputs && t.inputs.length ? `Read first:\n${bullet(t.inputs)}` : '',
    t.reuse && t.reuse.length ? `Use these existing tools; do not re-implement what they do:\n${bullet(t.reuse)}` : '',
    boot && t.bootstrap.build ? `Build spec (bootstrap):\n${bullet(t.bootstrap.build)}` : '',
    boot && t.bootstrap.reuse ? `Bootstrap reuse list:\n${bullet(t.bootstrap.reuse)}` : '',
    `Deliverables:\n${bullet(t.deliverables)}`,
    `Acceptance (run every command exactly as written; all must pass):\n${bullet(acc)}`,
    'Report every acceptance command in acceptance with cmd copied verbatim, its exit_code and passed. A missing or reworded command, or a non-zero exit code, makes the task blocked.',
    ver.length ? `Read-only checks the reviewers will run:\n${bullet(ver)}` : '',
    upstream.length ? `Upstream results:\n${bullet(upstream.map(r => `${r.id}${r.reused ? ' (from an earlier session)' : ''}: ${r.summary}${r.notes && r.notes.length ? ` (notes: ${r.notes.join('; ')})` : ''}`))}` : '',
    attemptText(t.id, t.fanout),
    findings && findings.length ? `Fix these first:\n${bullet(findings)}` : '',
    'Iterate until every acceptance command passes. If you cannot, stop and report status "blocked" with the failing command and what you need. Put changes needed outside your paths in requests ("dependency: <spec>" for a missing package), steps only a person or a Windows machine can do in manual_steps, and assumptions and questions in notes. Do not commit or push.',
  ].filter(Boolean).join('\n\n')
}

// ---------- task execution ----------
const instances = new Map() // task or item id -> { task, upstream }
const itemsOf = new Map() // fan-out id -> item instance ids
const usedItems = {}
const blocked = (id, kind, summary, extra) => ({ id, kind, status: 'blocked', summary, files_changed: [], acceptance: [], ...(extra || {}) })

async function build(t, upstream, findings, round) {
  instances.set(t.id, { task: t, upstream })
  const prev = PRIOR[t.id]
  if (!findings && prev && prev.status === 'done' && !rerunOf(t.id, t.fanout) && upstream.every(u => u.reused)) {
    return { ...prev, id: t.id, kind: 'build', reused: true }
  }
  const label = round ? `${t.id}:fix${round}` : t.id
  const slot = slotFor(label)
  const acc = (t.acceptance || []).map(c => anchor(c, slot))
  const ver = (t.verify || []).map(c => anchor(c, slot))
  let r
  try {
    r = await spawn(taskPrompt(t, upstream, findings, acc, ver, slot), opts(label, round ? 'Fix' : 'Build', resultSchema(acc), t.effort))
  } catch (e) {
    return blocked(t.id, 'build', `driver: ${e && e.message}`)
  }
  if (!r) return blocked(t.id, 'build', 'agent returned nothing (skipped or failed)')
  const { missing, failed } = checkCmds(acc, r.acceptance)
  const res = { ...r, id: t.id, kind: 'build', reused: false }
  if (r.status === 'done' && (missing.length || failed.length)) {
    return { ...res, status: 'blocked', summary: `claimed done, but ${failed.length} acceptance check(s) failed and ${missing.length} were not reported: ${[...failed, ...missing].join(' | ')}` }
  }
  return res
}

async function fanout(t, upstream) {
  let items = knownItems(t.id)
  if (!items) {
    if (!t.scout) return [blocked(t.id, 'fanout', `no items: pass args.items["${t.id}"] (see the dry run's items_from)`)]
    const scoutPrompt = [
      preamble(),
      `Scout for ${t.id}: ${t.title}`,
      t.scout,
      `Return items as { id, title, scope }. Item ids match ${ITEM_PATTERN.source}, are not Python keywords, not ${[...ITEM_RESERVED].join(', ')}, and not the app slug ${SLUG}; they are unique. Put anything you cannot place in uncovered. Do not edit files.`,
    ].join('\n\n')
    let s
    try { s = await spawn(scoutPrompt, opts(`${t.id}:scout`, 'Build', scoutSchema(), 'medium')) } catch (e) { return [blocked(t.id, 'fanout', `driver: ${e && e.message}`)] }
    if (!s) return [blocked(t.id, 'fanout', 'scout returned nothing')]
    if (s.uncovered && s.uncovered.length) return [blocked(t.id, 'fanout', `scout could not place: ${s.uncovered.join(', ')}`, { items: s.items })]
    items = s.items
  }
  const bad = itemProblem(t, items)
  if (bad) return [blocked(t.id, 'fanout', `${t.id}: ${bad}`, { items })]
  const insts = items.map(it => fillEach(t, it))
  const left = insts.flatMap(x => { const l = []; walk(x, x.id, (p, s) => { if ((s.match(ANY_PH) || []).length) l.push(p) }); return l })
  if (left.length) return [blocked(t.id, 'fanout', `unresolved placeholders in items: ${left.join(', ')}`, { items })]
  usedItems[t.id] = items
  itemsOf.set(t.id, insts.map(x => x.id))
  log(`${t.id}: fanning out over ${items.map(i => i.id).join(', ')}`)
  const out = await pipeline(insts, inst => build(inst, upstream))
  return out.map((r, i) => r || blocked(insts[i].id, 'build', 'item failed'))
}

function card(r, slot) {
  const inst = instances.get(r.id)
  if (!inst) return `### ${r.id} [${r.status}]: ${r.summary}`
  const t = inst.task
  return [
    `### ${r.id} [${r.status}${r.reused ? ', from an earlier session' : ''}]: ${t.title}`,
    `Goal: ${t.goal}`,
    `Owns: ${t.owns.join(', ')}${t.owns_except && t.owns_except.length ? ` (except ${t.owns_except.join(', ')})` : ''}`,
    `Deliverables:\n${bullet(t.deliverables)}`,
    `Acceptance:\n${bullet((t.acceptance || []).map(c => anchor(c, slot)))}`,
    t.verify && t.verify.length ? `Verify (read-only; run these):\n${bullet(t.verify.map(c => anchor(c, slot)))}` : 'Verify: none listed; run an acceptance command only if it writes nothing.',
    `Builder's summary: ${r.summary}`,
    r.files_changed && r.files_changed.length ? `Files changed:\n${bullet(r.files_changed)}` : '',
    r.notes && r.notes.length ? `Notes:\n${bullet(r.notes)}` : '',
  ].filter(Boolean).join('\n')
}

async function review(t, scope, round) {
  const lenses = t.lenses && t.lenses.length ? t.lenses : ['correctness against the deliverables and acceptance']
  const ids = scope.map(r => r.id)
  if (!ids.length) return { id: t.id, kind: 'review', status: 'blocked', summary: 'nothing to review: no done task in scope', issues: [], acceptance: [] }
  const verdicts = await parallel(lenses.map((lens, i) => () => {
    const label = `${t.id}:r${i + 1}${round ? `:round${round}` : ''}`
    const slot = slotFor(label)
    return spawn([
      preamble(),
      `You are reviewer ${i + 1} of ${lenses.length} for ${t.id}${round ? ` (re-review after fix round ${round})` : ''}. Lens: ${lens}`,
      `Your MP_DB_SUFFIX slot is ${slot}; the commands below are anchored with it. Write any outputs (quiet summaries, regenerated design files, synthetic results) under ${PATHS.private}/review/${slot}/. That directory is in the design zone: you may read and diff your outputs there. Never read a data-zone path (listed above), not even to compare it.`,
      `Stage exit criteria (the integrate step runs these; do not run them):\n${bullet(EXIT)}`,
      `Work under review:\n\n${scope.map(r => card(r, slot)).join('\n\n')}`,
      `Try to REFUTE that this work is done. Run each task's verify commands. For a task without them, run an acceptance command only if it writes nothing (no --out or --into without --check, no record, no --replace). Read the changes in both repos (git status, git diff). Do not edit files, commit or push. If you cannot find evidence that a task is done, report a blocking issue against that task saying which evidence is missing. Attribute every issue to exactly one of: ${ids.join(', ')}. Mark an issue blocking only if it breaks a deliverable, an acceptance command, a rule above or the stage exit criteria; otherwise minor. Give evidence for every issue (command and exit code, or file and line). Return an empty issues list if you find nothing.`,
    ].join('\n\n'), opts(label, 'Review', verdictSchema(ids), t.effort || 'high'))
  }))
  const missing = lenses.filter((_, i) => !verdicts[i])
  const issues = verdicts.flatMap((v, i) => (v && v.issues ? v.issues.map(x => ({ review: t.id, lens: i + 1, ...x })) : []))
  const nBlocking = issues.filter(x => x.severity === 'blocking').length
  return {
    id: t.id, kind: 'review',
    status: missing.length ? 'blocked' : 'done',
    summary: `${lenses.length - missing.length}/${lenses.length} reviewers answered${missing.length ? ` (missing: ${missing.map(l => l.slice(0, 60)).join(' | ')})` : ''}; ${nBlocking} blocking, ${issues.length - nBlocking} minor`,
    issues, acceptance: [],
  }
}

const memo = new Map()
const expand = id => itemsOf.get(id) || [id]
function run(id) {
  if (!memo.has(id)) {
    memo.set(id, (async () => {
      const t = TASKS.get(id)
      try {
        const upstream = (await Promise.all(t.depends_on.map(run))).flat()
        const notDone = upstream.filter(r => r.kind !== 'review' && r.status !== 'done')
        if (notDone.length) return [{ id, kind: t.kind, status: 'skipped', summary: `skipped: upstream not done (${notDone.map(r => r.id).join(', ')})`, files_changed: [], acceptance: [], issues: [] }]
        if (t.kind === 'build') return [await build(t, upstream)]
        if (t.kind === 'fanout') return await fanout(t, upstream)
        const scope = (await Promise.all([...ancestors(id)].map(run))).flat().filter(r => r.kind !== 'review' && r.status === 'done')
        return [await review(t, scope, 0)]
      } catch (e) {
        return [blocked(id, t.kind, `driver error: ${e && e.message}`, { issues: [] })]
      }
    })())
  }
  return memo.get(id)
}

// ---------- build and review ----------
phase('Build')
const firstPass = (await Promise.all(WORK.map(t => run(t.id)))).flat()
const latest = new Map(firstPass.map(r => [r.id, r]))
const blockingOf = () => [...latest.values()].filter(r => r.kind === 'review').flatMap(r => (r.issues || []).filter(x => x.severity === 'blocking'))
const scopeFor = t => [...ancestors(t.id)].flatMap(expand).map(id => latest.get(id)).filter(r => r && r.kind !== 'review' && r.status === 'done')
let blocking = blockingOf()
let FIX_ROUNDS = 0

// ---------- fix rounds: the named tasks plus everything downstream, in dependency order ----------
for (let round = 1; round <= MAX_FIX_ROUNDS && blocking.length; round++) {
  const byTask = new Map()
  for (const x of blocking) {
    if (!instances.has(x.task_id)) { log(`finding names a task that did not run: ${x.task_id}: ${x.problem}`); continue }
    if (!byTask.has(x.task_id)) byTask.set(x.task_id, [])
    byTask.get(x.task_id).push(`Reviewer finding (${x.review} lens ${x.lens}): ${x.problem}${x.evidence ? ` (evidence: ${x.evidence})` : ''}`)
  }
  if (!byTask.size) break
  phase('Fix')
  FIX_ROUNDS = round
  const depIds = id => instances.get(id).upstream.map(u => u.id)
  const redo = new Set(byTask.keys())
  for (let grew = true; grew;) {
    grew = false
    for (const id of instances.keys()) if (!redo.has(id) && depIds(id).some(d => redo.has(d))) { redo.add(id); grew = true }
  }
  log(`fix round ${round}: fix ${[...byTask.keys()].join(', ')}; re-run downstream: ${[...redo].filter(x => !byTask.has(x)).join(', ') || 'none'}`)
  const fixMemo = new Map()
  const redoOne = id => {
    if (!fixMemo.has(id)) {
      fixMemo.set(id, (async () => {
        const changed = depIds(id).filter(d => redo.has(d))
        const ups = await Promise.all(changed.map(redoOne))
        const bad = ups.filter(u => u.status !== 'done')
        if (bad.length) return { id, kind: 'build', status: 'skipped', summary: `skipped: upstream fix not done (${bad.map(u => u.id).join(', ')})`, files_changed: [], acceptance: [] }
        const inst = instances.get(id)
        const f = [...(byTask.get(id) || [])]
        if (changed.length) f.push(`Upstream ${changed.join(', ')} changed in fix round ${round}. Regenerate your outputs from the new inputs and re-run every acceptance command; change nothing else.`)
        return build(inst.task, inst.upstream.map(u => latest.get(u.id) || u), f, round)
      })())
    }
    return fixMemo.get(id)
  }
  const out = await Promise.all([...redo].map(redoOne))
  out.forEach(r => latest.set(r.id, r))
  if (out.some(r => r.status !== 'done')) { log(`fix round ${round} left work not done; skipping the re-review`); break }
  const again = REVIEWS.filter(t => [...ancestors(t.id)].flatMap(expand).some(id => redo.has(id)))
  const reviewed = await parallel(again.map(t => () => review(t, scopeFor(t), round)))
  reviewed.forEach((r, i) => latest.set(again[i].id, r || { id: again[i].id, kind: 'review', status: 'blocked', summary: 're-review failed', issues: [], acceptance: [] }))
  blocking = blockingOf()
}

// ---------- report ----------
const final = [...latest.values()]
const notDone = final.filter(r => r.status !== 'done')
const requests = final.flatMap(r => (r.requests || []).map(q => `${r.id}: ${q}`))
const issues = final.filter(r => r.kind === 'review').flatMap(r => r.issues || [])
const budgetSpent = () => (typeof budget !== 'undefined' && budget && typeof budget.spent === 'function' ? budget.spent() : null)
const blockersOf = extra => [
  ...notDone.map(r => `${r.id} ${r.status}`),
  ...(blocking.length ? [`${blocking.length} blocking finding(s)`] : []),
  ...(extra || []),
]
function makeReport(extra) {
  return {
    engagement: E.id, stage: S.id, mode: MODE, attempt: ATTEMPT, change_order: CO, branches: BRANCH,
    ready: false, blockers: blockersOf(),
    tasks: final.map(r => ({ id: r.id, status: r.status, summary: r.summary, notes: r.notes || [], reused: !!r.reused })),
    results: Object.fromEntries(final.filter(r => r.kind !== 'review').map(r => [r.id, r])),
    items: usedItems,
    issues, blocking, requests,
    manual_steps: final.flatMap(r => (r.manual_steps || []).map(m => ({ task: r.id, ...m }))),
    pipeline_changes: final.flatMap(r => (r.pipeline_changes || []).map(c => `${r.id}: ${c}`)),
    agents: { estimate: EST, spawned: SPAWNED, output_tokens_turn: budgetSpent(), note: 'output_tokens_turn counts output tokens of the whole turn, main loop included; per-run token columns come from the run directory (runbook)' },
    fix_rounds_used: FIX_ROUNDS,
    gate: GATE,
    ...(extra || {}),
  }
}

// Staging: owned paths of done tasks, by repo, repo-relative. private: paths are never staged.
const REPO_DIR = { pipeline: PATHS.pipeline, target: PATHS.target_repo, records: PATHS.records }
const joinRel = (a, b) => (a ? (b ? `${a}/${b}` : `${a}/`) : b)
function toRepo(o) {
  const i = String(o).indexOf(':')
  const root = o.slice(0, i); const rest = o.slice(i + 1)
  if (root === 'pipeline') return ['pipeline', rest]
  if (root === 'target-repo') return ['target', rest]
  if (root === 'target') return ['target', joinRel(TARGET_REL, rest)]
  if (root === 'records') return RECORDS_IN_PIPELINE ? ['pipeline', joinRel(RECORDS_REL, rest)] : ['records', rest]
  return null
}
const REPOS = ['pipeline', 'target', ...(RECORDS_IN_PIPELINE ? [] : ['records'])]
function stagingPlan(results, extraOwned) {
  const plan = Object.fromEntries(REPOS.map(k => [k, { add: new Set(), exclude: new Set() }]))
  for (const r of results) {
    const inst = instances.get(r.id)
    if (!inst || r.status !== 'done') continue
    for (const o of inst.task.owns || []) { const x = toRepo(o); if (x) plan[x[0]].add.add(x[1]) }
    for (const o of inst.task.owns_except || []) { const x = toRepo(o); if (x) plan[x[0]].exclude.add(x[1]) }
  }
  for (const o of extraOwned || []) { const x = toRepo(o); if (x) plan[x[0]].add.add(x[1]) }
  const parts = REPOS.filter(k => plan[k].add.size).map(k => {
    const add = [...plan[k].add]; const ex = [...plan[k].exclude]
    // An excluded path (or one under it) that another done task owns is staged again after the reset.
    const readd = add.filter(a => ex.some(e => a === e || a.startsWith(e)))
    return [
      `${k} repo (${REPO_DIR[k]}): run \`git -C ${REPO_DIR[k]} add -A -- '<path>'\` for each path below that exists (skip one git reports as matching no files or as ignored):`,
      bullet(add),
      ex.length ? `then unstage these, which other tasks own: \`git -C ${REPO_DIR[k]} reset -q -- '<path>'\` for each of:\n${bullet(ex)}` : '',
      readd.length ? `then stage these again, because another done task owns them: \`git -C ${REPO_DIR[k]} add -A -- '<path>'\` for each of:\n${bullet(readd)}` : '',
    ].filter(Boolean).join('\n')
  })
  return parts.length ? parts.join('\n') : 'Nothing to stage: no task is done.'
}
const RECORD_FILES = ['records:log.md', `records:runs/${RUNS_FILE}`]
const commitText = (msg, slot) => `commit each repo that has staged changes with the message "${msg}"${A.commit_trailer ? ', then a blank line and the trailer below' : ''}. ${pushText(slot)}${A.commit_trailer ? `\nTrailer:\n${A.commit_trailer}` : ''}`
function branchCheck(skipTarget) {
  return `Branch check: \`git -C ${PATHS.pipeline} branch --show-current\` must print ${BRANCH.pipeline}${skipTarget ? '' : ` and \`git -C ${PATHS.target_repo} branch --show-current\` must print ${BRANCH.target}`}. For a repo that is on another branch, stage and commit nothing there and report it with pushed false.`
}
function logRow(ready, blockers) {
  const cell = s => String(s).replace(/\|/g, '/').replace(/\s+/g, ' ')
  return `| DATE | ${S.id} | ${ATTEMPT} | pending | ${MODE}${CO ? ` ${CO}` : ''} | ${SPAWNED + 1} | ${EST.typical}/${EST.worst} | ${FIX_ROUNDS} | see runbook | see runbook | see runbook | see runbook | see runbook | pending | pending | pending | ${ready ? 'yes' : 'no'} | ${cell(blockers.length ? blockers.join('; ') : 'none')} |`
}
// A client target repo (bootstrap false, target.mode github) is pushed only through mp-data, which uses the
// engagement's push token (secrets.github_push.ref) and never prints it. Pipeline, records and demo repos
// use the operator's own git credentials. No -u: inside mp-run .git/config is read-only, so git could
// not record the upstream and would print a warning.
function pushText(slot) {
  const target = TARGET_MODE === 'local'
    ? ' (target.mode is local: commit the target repo on its stage branch and never push it)'
    : !BOOT
      ? `, and push the client target repo only with the data-plane call \`${WRAPPER} --slot ${slot} push ${ENG_FILE} --branch ${BRANCH.target}\` (it uses the engagement's push token; never run git push there)`
      : ` and \`git -C ${PATHS.target_repo} push origin ${BRANCH.target}\``
  return `Push with \`git -C ${PATHS.pipeline} push origin ${BRANCH.pipeline}\`${target}; retry a network error up to 4 times, waiting 2, 4, 8 and 16 seconds.${RECORDS_IN_PIPELINE ? '' : ` Commit the records repo ${PATHS.records} too; push it only if \`git -C ${PATHS.records} remote\` prints a remote.`} If a repo has nothing new to commit, report its current HEAD and still push its branch.`
}
// Git and date commands of integrate and checkpoint run inside mp-run (the operator's own gh file-stored token),
// except in an S0 bootstrap run. Client sessions block every Bash call that is not one mp-run or mp-data
// call; demos follow the same form from S1 on to rehearse it.
const gitRule = slot => {
  if (BRACED) return ''
  const how = `run each git and date command in these steps as \`${RUNNER} --eng ${ENG_FILE} --slot ${slot} -- '<command>'\` (each ' inside written '\\''), and the data-plane calls as written. Inside ${RUNNER} git and gh authenticate only with gh's file-stored token (the keyring and ssh-agent are hidden); if a push fails to authenticate, report it in notes and never set up other credentials.`
  return BOOT ? `As in a client session, ${how}` : `This session allows only ${RUNNER} and ${WRAPPER} calls: ${how}`
}
const GUARD = `${WRAPPER} guard ${ENG_FILE} --repo ${PATHS.pipeline} --repo ${PATHS.target_repo}`
// Demo target repos are public or become public at the northwind S6 gate: every push is preceded by a
// history scan, cheap on demo repos, so a leak is caught before the flip.
const HISTORY = BOOT && TARGET_MODE === 'github'

function reposOk(res) {
  const want = { pipeline: BRANCH.pipeline, target: BRANCH.target }
  return !!res && REPOS.every(k => {
    const r = (res.repos || []).find(x => x && x.repo === k)
    if (!r || typeof r.commit !== 'string' || !/^[0-9a-f]{7,40}$/.test(r.commit)) return false
    if (k === 'records') return true
    if (r.branch !== want[k]) return false
    return k === 'target' && TARGET_MODE === 'local' ? true : r.pushed === true
  })
}

async function checkpoint(report) {
  if (A.checkpoint === false) { log('checkpoint disabled (args.checkpoint false); nothing committed'); return null }
  const slot = slotFor(`${S.id}:checkpoint`)
  const bootS0 = BOOT && S.id === 'S0'
  const regex = "[.](accdt|accdb|accde|mdb|mde|laccdb|ldb|mdw|xlsm|xlsb|xls|xlam|bas|cls|frm|frx|msg|bak|pdf|eml|xlsx)$|^(private|data|vendor)/"
  const fallback = `test -z "$(git -C ${PATHS.pipeline} diff --cached --name-only | grep -Ev '^toolkit/tests/fixtures/' | grep -Ei '${regex}')"`
  const guards = [`${GUARD} --staged`, ...(HISTORY ? [`${GUARD} --history`] : [])].map(c => anchor(c, slot))
  const checks = [...guards, ...(bootS0 ? [anchor(fallback, slot)] : [])]
  const rows = report.blockers
  const prompt = [
    preamble(),
    `Checkpoint stage ${S.id} for ${E.id}, attempt ${ATTEMPT}. The run is NOT ready (${rows.join('; ') || 'see the report'}). Save the finished work so a later session can continue; do not integrate.`,
    branchCheck(false),
    `Stage only the owned paths of the tasks that are done; never anything under ${PATHS.private}, never \`git add -A\` without paths or \`git add .\`:\n${stagingPlan(final)}`,
    `Run \`git -C <repo> status --porcelain=v1 -uall\` in each repo. List every changed or untracked path outside the staged paths in unowned_changes and leave it unstaged.`,
    gitRule(slot),
    `Check the staged files${HISTORY ? ' and the history of both repos' : ''} by running exactly:\n${bullet(guards)}${bootS0 ? `\nIf \`command -v ${WRAPPER}\` finds nothing or ${PATHS.pipeline}/toolkit/commands/guard.py does not exist yet (first S0), run this instead, and do not stage or commit anything in the target repo:\n${bullet([checks[checks.length - 1]])}` : ''}\nReport each command you ran in checks with its exit_code. If one fails, run \`git -C <repo> reset -q\` in each repo and commit nothing.`,
    `If the check passed: append this row to ${PATHS.records}/log.md, replacing DATE with the output of \`date -u +%F\`:\n${logRow(false, rows)}\nWrite this JSON to ${PATHS.records}/runs/${RUNS_FILE} (create runs/ if needed), then stage both files:\n\`\`\`json\n${JSON.stringify({ ...report, ready: false })}\n\`\`\``,
    `Then ${commitText(`WIP ${S.id} ${E.id}: attempt ${ATTEMPT} not ready`, slot)}`,
    'Report each repo (pipeline, target' + (RECORDS_IN_PIPELINE ? '' : ', records') + ') with its current branch, HEAD commit SHA and whether the branch is pushed.',
  ].filter(Boolean).join('\n\n')
  log(`${S.id} not ready; spawning the checkpoint agent`)
  try {
    return await spawn(prompt, opts(`${S.id}:checkpoint`, 'Integrate', checkpointSchema(checks), 'low'), true)
  } catch (e) {
    log(`checkpoint failed: ${e && e.message}`)
    return null
  }
}

if (notDone.length || blocking.length) {
  log(`${S.id} not ready: ${notDone.length} task(s) not done, ${blocking.length} blocking finding(s)`)
  phase('Integrate')
  const report = makeReport()
  const cp = await checkpoint(report)
  return { ...report, agents: { ...report.agents, spawned: SPAWNED, output_tokens_turn: budgetSpent() }, ...(cp || A.checkpoint !== false ? { checkpoint: cp } : {}) }
}

// ---------- integrate ----------
phase('Integrate')
const STAGED_CHECKS = [
  `${GUARD} --staged`,
  'uv run pre-commit run',
  `cd ${PATHS.target_repo} && uv run --project ${PATHS.pipeline} pre-commit run`,
  ...(HISTORY ? [`${GUARD} --history`] : []),
]
const ICHECKS = [...STAGED_CHECKS.map(c => anchor(c, ISLOT)), ...EXIT]
const preReport = makeReport()
const integratePrompt = [
  preamble(),
  `Integrate stage ${S.id} for ${E.id}, attempt ${ATTEMPT}. Every task reports done and the reviews have no blocking findings.`,
  branchCheck(false),
  gitRule(ISLOT),
  requests.length ? `Tasks asked for changes outside their own paths. Apply the ones that are small and clearly inside this stage, and list each one in requests_applied with the paths you changed; put the rest in notes:\n${bullet(requests)}` : '',
  `Stage only these paths, plus files you changed for requests; never anything under ${PATHS.private}, never \`git add -A\` without paths or \`git add .\`:\n${stagingPlan(final)}`,
  `Run \`git -C <repo> status --porcelain=v1 -uall\` in each repo. Every changed or untracked path must be under a staged path above or be a file listed in requests_applied. Report any other path in unowned_changes and leave it unstaged; then commit nothing.`,
  `Run these checks exactly as written, in this order, and report each one verbatim in exit_criteria with its exit_code and passed (the first ones check the staged files${HISTORY ? ' and the history of both repos' : ''}; the rest are the stage exit criteria):\n${bullet(ICHECKS)}`,
  `Only if every check passed and unowned_changes is empty: append this row to ${PATHS.records}/log.md, replacing DATE with the output of \`date -u +%F\`:\n${logRow(true, [])}\nWrite this JSON to ${PATHS.records}/runs/${RUNS_FILE} (create runs/ if needed) and stage both files with:\n${stagingPlan([], RECORD_FILES)}\n\`\`\`json\n${JSON.stringify({ ...preReport, ready: true, blockers: [] })}\n\`\`\`\nThen ${commitText(S.commit_message, ISLOT)}`,
  'If any check fails, commit nothing and say why in notes.',
  'Report each repo (pipeline, target' + (RECORDS_IN_PIPELINE ? '' : ', records') + ') with its current branch, HEAD commit SHA and whether the branch is pushed.',
].filter(Boolean).join('\n\n')
let done = null
try {
  done = await spawn(integratePrompt, opts(`${S.id}:integrate`, 'Integrate', integratedSchema(ICHECKS)))
} catch (e) {
  log(`integrate not started: ${e && e.message}`)
}
const ex = done ? checkCmds(ICHECKS, done.exit_criteria) : { missing: ICHECKS, failed: [] }
const unowned = done ? done.unowned_changes || [] : []
const repos = reposOk(done)
const ready = !!done && !ex.missing.length && !ex.failed.length && !unowned.length && repos
const extraBlockers = ready ? [] : [
  ...(done ? [] : ['integrate agent returned nothing']),
  ...(ex.failed.length ? [`${ex.failed.length} exit check(s) failed`] : []),
  ...(done && ex.missing.length ? [`${ex.missing.length} exit check(s) not reported`] : []),
  ...(unowned.length ? [`unowned changes: ${unowned.join(', ')}`] : []),
  ...(done && !repos ? ['a repo is not committed on its stage branch or not pushed'] : []),
]
let report = { ...makeReport(), ready, blockers: ready ? [] : blockersOf(extraBlockers), integrate: done, exit_missing: ex.missing, exit_failed: ex.failed }
if (!ready) {
  log(`${S.id} integrate did not finish: ${extraBlockers.join('; ')}`)
  const cp = await checkpoint(report)
  report = { ...report, ...(cp || A.checkpoint !== false ? { checkpoint: cp } : {}) }
} else {
  log(`${S.id} ready: committed and pushed on ${BRANCH.pipeline} / ${BRANCH.target}`)
}
return { ...report, agents: { ...report.agents, spawned: SPAWNED, output_tokens_turn: budgetSpent() } }
