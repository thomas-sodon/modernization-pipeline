#!/usr/bin/env node
// Test harness for the stage driver (run-stage.workflow.js) with mocked agents. Node only, no npm packages.
//
//   node tests/run-stage.test.mjs                       exits 0 when every check passes, 1 otherwise
//
// Inputs (environment variables override the defaults, which are resolved next to this file):
//   DRIVER           the driver script     default ../run-stage.workflow.js, else ../../.claude/workflows/run-stage.js
//   PIPELINE_JSON    pipeline.json         default ../pipeline.json, else ../../pipeline.json
//   ENGAGEMENTS_DIR  engagements/          default ../engagements, else ../../engagements
//   MAKE_LAUNCHER    make-launcher.py      default next to the driver: ../make-launcher.py, else ../../.claude/workflows/make-launcher.py
//   VERBOSE=1        print every check, not only failures
//
// What it does: checks the Workflow-tool constraints on the script, runs a dry run of every stage for
// northwind-demo, invoicegen-demo and a filled client engagement made from _template (no problems
// allowed), runs make-launcher.py for the same dry runs (the launcher, with pipeline.json sliced, must
// return exactly the dry run of the full file and stay under 500000 bytes; args holding pipeline exit 2),
// runs every stage with mocked agents that pass (ready, agent count equals the estimate,
// no unfilled placeholders, no concurrent owners of one path), checks the command forms (data-plane
// calls unwrapped with --slot and the literal slot, mp-run with exact quoting, the braced S0-bootstrap
// form, environment commands never wrapped, compound mp-data lines refused everywhere, multi-line agent and
// items_from commands refused, environment commands exempt from the one-line rule only), the reference
// build forms and the S5/S6 app sync at the head of prepare (decisions L13.3, L13.7), the environment
// list derived from environment[].stages/kinds/when
// (jq right after uv and before every item that calls it), stage offers keys, client session prompts and
// pushes (never -u), demo history checks at integrate and checkpoint and the reviewer zone rule, replays
// the integrate and checkpoint staging lists (add, reset, then re-add of excluded paths another done task
// owns) on sample paths, checks that the unedited _template is refused, runs
// the failure scenarios (missing acceptance report, empty exit criteria, fix rounds with descendants,
// reviewer failure, bad fan-out items, prior reuse, rerun, checkpoint, skip_if, lenses_by_kind, offers
// and gates, target.mode local, database.stage1 at S3, change-order report file), and prints the agent
// estimate table used in proposal §7 and §13.4.
// When bash and jq are on PATH it also runs the mssql item's `when` against each engagement. The launcher
// checks need python3 on PATH; without it they are skipped with a note.

import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { isDeepStrictEqual } from 'node:util'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const first = (...cands) => cands.find(p => p && fs.existsSync(p)) || cands[0]
const DRIVER = process.env.DRIVER || first(path.join(HERE, '../run-stage.workflow.js'), path.join(HERE, '../../.claude/workflows/run-stage.js'))
const PIPELINE_JSON = process.env.PIPELINE_JSON || first(path.join(HERE, '../pipeline.json'), path.join(HERE, '../../pipeline.json'))
const ENG_DIR = process.env.ENGAGEMENTS_DIR || first(path.join(HERE, '../engagements'), path.join(HERE, '../../engagements'))
const MAKE_LAUNCHER = process.env.MAKE_LAUNCHER || first(path.join(path.dirname(DRIVER), 'make-launcher.py'), path.join(HERE, '../make-launcher.py'), path.join(HERE, '../../.claude/workflows/make-launcher.py'))
const VERBOSE = process.env.VERBOSE === '1'

// ---------- tiny assertion kit ----------
let passed = 0
const failed = []
let section = ''
function head(title) { section = title; console.log(`\n== ${title}`) }
function check(name, cond, detail) {
  if (cond) { passed++; if (VERBOSE) console.log(`  ok   ${name}`) } else {
    failed.push(`[${section}] ${name}`)
    console.log(`  FAIL ${name}${detail === undefined ? '' : `\n       ${String(typeof detail === 'string' ? detail : JSON.stringify(detail)).split('\n').join('\n       ')}`}`)
  }
}
const clone = o => JSON.parse(JSON.stringify(o))
const readJson = (p, what) => {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')) } catch (e) { console.log(`cannot read ${what} at ${p}: ${e.message}`); process.exit(2) }
}

console.log(`driver:      ${DRIVER}\npipeline:    ${PIPELINE_JSON}\nengagements: ${ENG_DIR}\nlauncher:    ${MAKE_LAUNCHER}`)
const SRC = fs.readFileSync(DRIVER, 'utf8')
const PIPE = readJson(PIPELINE_JSON, 'pipeline.json')
const ENG = {}
for (const id of ['northwind-demo', 'invoicegen-demo', '_template']) ENG[id] = readJson(path.join(ENG_DIR, id, 'engagement.json'), `${id}/engagement.json`)
const STAGES = PIPE.stages.map(s => s.id)

// ---------- static checks on the Workflow script ----------
head('Workflow script constraints')
const metaStart = SRC.search(/^export const meta = \{/m)
check('script has "export const meta = {" at the start of a line', metaStart >= 0)
check('everything before meta is comments or blank', /^(\s*(\/\/[^\n]*)?\n)*$/.test(SRC.slice(0, metaStart)), SRC.slice(0, metaStart).split('\n').find(l => l.trim() && !l.trim().startsWith('//')))
let metaText = ''
{
  let depth = 0, i = SRC.indexOf('{', metaStart), quote = null
  const start = i
  for (; i < SRC.length; i++) {
    const c = SRC[i]
    if (quote) { if (c === '\\') i++; else if (c === quote) quote = null; continue }
    if (c === "'" || c === '"') quote = c
    else if (c === '`') { quote = '`' }
    else if (c === '{') depth++
    else if (c === '}') { depth--; if (depth === 0) break }
  }
  metaText = SRC.slice(start, i + 1)
}
check('meta is a pure literal (no backticks, spreads or calls)', !/`|\.\.\.|\w\s*\(/.test(metaText.replace(/'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"/g, "''")), metaText.slice(0, 200))
let META = null
try { META = new Function(`"use strict"; return (${metaText})`)() } catch (e) { check('meta evaluates', false, e.message) }
check('meta has name and description', !!(META && META.name && META.description))
const codeOnly = SRC.replace(/\/\/[^\n]*/g, '')
for (const [name, re] of [['Date.now()', /Date\.now\s*\(/], ['Math.random()', /Math\.random\s*\(/], ['argless new Date()', /new Date\s*\(\s*\)/], ['require()', /\brequire\s*\(/], ['import', /^\s*import\s/m], ['process.', /\bprocess\./], ['a second export', /^export (?!const meta)/m]]) {
  check(`script does not use ${name}`, !re.test(codeOnly))
}
const AsyncFunction = (async () => {}).constructor
let DRIVER_FN
try {
  DRIVER_FN = new AsyncFunction('agent', 'parallel', 'pipeline', 'log', 'phase', 'args', 'budget', 'workflow', SRC.replace(/^export const meta/m, 'const meta'))
  check('script parses as plain JavaScript', true)
} catch (e) { check('script parses as plain JavaScript', false, e.message); report() }
const PHASES = new Set(((META && META.phases) || []).map(p => p.title))

// ---------- mocked Workflow globals ----------
const sleep = ms => new Promise(r => setTimeout(r, ms))
function hash(s) { let h = 0; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) >>> 0; return h }
const prop = (o, k) => !!(o && o.schema && o.schema.properties && o.schema.properties[k])
const isResult = o => prop(o, 'status') && prop(o, 'acceptance')
const isVerdict = o => prop(o, 'issues') && !prop(o, 'status')
const isScout = o => prop(o, 'uncovered')
const isIntegrate = o => prop(o, 'exit_criteria') && prop(o, 'repos')
const isCheckpoint = o => prop(o, 'checks') && prop(o, 'repos')
const enumOf = (o, k) => { const it = o.schema.properties[k].items; return (it && it.properties && it.properties.cmd && it.properties.cmd.enum) || [] }
const fillBranch = (tpl, eng, stage) => tpl.replace(/\{\{e\.id\}\}/g, eng.id).replace(/\{\{s\.id\}\}/g, stage.id).replace(/\{\{s\.slug\}\}/g, stage.slug)
const inside = (p, d) => p === d || p.startsWith(`${d}/`)

// The mocked scout returns one item per areas_hint entry (up to the fan-out's max_items), else two items.
function hintItems(eng, label) {
  const fan = PIPE.stages.flatMap(s => s.tasks).find(t => `${t.id}:scout` === label)
  const hint = (eng.scope && eng.scope.areas_hint) || []
  const ids = hint.length ? hint : ['orders', 'inventory']
  return ids.slice(0, (fan && fan.max_items) || 4).map(id => ({ id, title: id.replace(/_/g, ' '), scope: `query: ${id} list` }))
}
async function runDriver(a, scenario = {}) {
  const calls = []
  const logs = []
  const phases = []
  const overlaps = []
  const active = new Map()
  let running = 0, peak = 0
  const queue = []
  const CAP = 6
  const pipe = a.pipeline
  const stage = pipe.stages.find(s => s.id === a.stage_id) || { id: a.stage_id, slug: '' }
  const branches = { pipeline: fillBranch(pipe.branches.pipeline, a.engagement, stage), target: fillBranch(pipe.branches.target, a.engagement, stage) }
  const recordsSeparate = !!(a.paths && a.paths.records && !inside(a.paths.records.replace(/\/+$/, ''), a.paths.pipeline))
  const repos = () => [
    { repo: 'pipeline', branch: branches.pipeline, commit: 'a1b2c3d4', pushed: true },
    { repo: 'target', branch: branches.target, commit: 'b2c3d4e5', pushed: true },
    ...(recordsSeparate ? [{ repo: 'records', branch: 'main', commit: 'c3d4e5f6', pushed: false }] : []),
  ]
  function defaultResult(prompt, o) {
    if (isVerdict(o)) return { issues: [] }
    if (isScout(o)) return { items: scenario.scoutItems || hintItems(a.engagement, o.label), uncovered: [] }
    if (isIntegrate(o)) return { exit_criteria: enumOf(o, 'exit_criteria').map(cmd => ({ cmd, exit_code: 0, passed: true })), repos: repos(), requests_applied: [], unowned_changes: [], notes: [] }
    if (isCheckpoint(o)) return { checks: enumOf(o, 'checks').slice(0, 1).map(cmd => ({ cmd, exit_code: 0, passed: true })), repos: repos(), unowned_changes: [], notes: [] }
    if (isResult(o)) return { status: 'done', summary: `did ${o.label}`, files_changed: [], acceptance: enumOf(o, 'acceptance').map(cmd => ({ cmd, exit_code: 0, passed: true })), notes: [] }
    return null
  }
  async function agent(prompt, o = {}) {
    if (running >= CAP) await new Promise(r => queue.push(r))
    running++
    peak = Math.max(peak, running)
    const ownsLine = (prompt.match(/^Paths you own \(create or edit only these\): (.*)$/m) || [])[1]
    const exceptLine = (prompt.match(/^Never edit \(inside your paths but owned by other tasks\): (.*)$/m) || [])[1]
    const owns = ownsLine ? ownsLine.split(', ') : []
    const except = exceptLine ? exceptLine.split(', ') : []
    const covers = (x, y) => x === y || (x.endsWith('/') && y.startsWith(x))
    for (const [lbl, other] of active) {
      for (const x of owns) for (const y of other.owns) {
        if ((covers(x, y) && !other.except.some(e => covers(e, y) || covers(e, x)) && !except.some(e => covers(e, y))) || (covers(y, x) && !except.some(e => covers(e, x) || covers(e, y)) && !other.except.some(e => covers(e, x)))) overlaps.push(`${o.label} and ${lbl}: ${x} / ${y}`)
      }
    }
    const key = `${o.label}#${calls.length}`
    active.set(key, { owns, except })
    calls.push({ label: o.label, phase: o.phase, effort: o.effort, prompt, opts: o })
    if (o.phase) phases.push(o.phase)
    try {
      await sleep(2 + (hash(o.label) % 9))
      let v = scenario.onAgent ? scenario.onAgent(prompt, o, { calls }) : undefined
      if (v === undefined) v = defaultResult(prompt, o)
      return v
    } finally {
      active.delete(key)
      running--
      const n = queue.shift()
      if (n) n()
    }
  }
  async function parallel(thunks) {
    return Promise.all(thunks.map(async t => { try { return await t() } catch (e) { logs.push(`parallel thunk threw: ${e.message}`); return null } }))
  }
  async function pipeline(items, ...stages) {
    return Promise.all(items.map(async (item, i) => {
      let v = item
      try { for (const st of stages) v = await st(v, item, i); return v } catch (e) { logs.push(`pipeline stage threw: ${e.message}`); return null }
    }))
  }
  const budget = { total: null, spent: () => 4242, remaining: () => Infinity }
  const workflow = async () => { throw new Error('workflow() is not used by the driver') }
  let result, error
  try { result = await DRIVER_FN(agent, parallel, pipeline, m => logs.push(m), t => phases.push(t), a, budget, workflow) } catch (e) { error = e.message }
  return { result, error, calls, logs, phases, overlaps, peak, labels: calls.map(c => c.label) }
}

// Runs one scenario block; an unexpected exception (for example a pipeline.json without a field the
// scenario needs) becomes a named failure instead of stopping the harness.
async function block(name, fn) {
  try { await fn() } catch (e) { check(`${name}: the scenario ran without a harness error`, false, `${e && e.stack ? e.stack.split('\n').slice(0, 3).join('\n') : e}`) }
}

// ---------- engagements and arguments ----------
const HOME = '/home/op'
const ENV_FILE = `${HOME}/.config/modernization/env`
const PIPELINE_DIR = '/w/modernization-pipeline'
const DEMO_REPO = '/w/access-excel-to-django'
function demoArgs(id, stage_id, extra = {}) {
  const e = clone(ENG[id])
  const root = e.target && e.target.root && e.target.root !== '.' ? `${DEMO_REPO}/${e.target.root}` : DEMO_REPO
  return {
    pipeline: PIPE, engagement: e, stage_id, prepared: stage_id,
    paths: { pipeline: PIPELINE_DIR, target_repo: DEMO_REPO, target: root, private: `${HOME}/engagements/${id}/private`, env_file: ENV_FILE },
    ...extra,
  }
}
// A client engagement made from _template: explicit values for the fields that matter, then every
// remaining <...> marker replaced by its first option (or a date), listed so template changes stay visible.
const CLIENT_ID = 'c2026001'
const CLIENT_RECORDS = `${HOME}/engagements/${CLIENT_ID}/record`
const CLIENT_REPO = `${HOME}/engagements/${CLIENT_ID}/target`
const autoFilled = []
function makeClient() {
  const c = clone(ENG._template)
  c.id = CLIENT_ID
  c.client = 'Acme Widgets Ltd: order entry and invoicing for 12 office staff, on Microsoft 365'
  Object.assign(c.source, { kind: 'accdt', path: '{{p.private}}/sources/acme-orders-fe.accdt', data_copy: '{{p.private}}/sources/data/acme-orders-be.accdb', layout: 'split', backends: [{ path: '{{p.private}}/sources/data/acme-orders-be.accdb', sha256: null, password_ref: null }] })
  c.target = { ...c.target, mode: 'github', repo: 'acme-widgets/acme-orders', root: '.', local_path: null }
  c.records = CLIENT_RECORDS
  c.app = { ...c.app, slug: 'acme_orders', project_name: 'Acme Orders', timezone: 'Europe/London' }
  if (c.database) { c.database.primary = 'mssql'; c.database.verify_also = ['postgresql'] }
  if (c.auth) c.auth.mode = 'microsoft-entra'
  c.hosting = { ...(c.hosting || {}), provider: 'azure-app-service', managed_by: 'client' }
  c.data_policy = { ...c.data_policy, redact: 'always', execution: 'local', ai_terms: { provider: 'Anthropic commercial terms', zero_data_retention: true, checked_on: '2026-10-01' }, client_consent: { document: 'MSA section 7: processing of code and schema by Anthropic', signed_on: '2026-10-02' } }
  if (c.scope) c.scope.must_keep = ['order entry to printable invoice', 'month-end sales report']
  c.commercial = { ...c.commercial, tier: 'stage2-standard', purchased: ['assessment', 'stage1', 'stage2-standard'] }
  const walkFill = (v, p) => {
    if (typeof v === 'string') {
      if (!/<[^<>]+>/.test(v)) return v
      autoFilled.push(p)
      return v.replace(/<([^<>]+)>/g, (m, inner) => (/YYYY-MM-DD/.test(inner) ? '2026-10-03' : inner.includes('|') ? inner.split('|')[0].trim() : 'filled-by-test'))
    }
    if (Array.isArray(v)) return v.map((x, i) => walkFill(x, `${p}[${i}]`))
    if (v && typeof v === 'object') { const o = {}; for (const k of Object.keys(v)) o[k] = k.startsWith('_') ? v[k] : walkFill(v[k], `${p}.${k}`); return o }
    return v
  }
  return walkFill(c, 'engagement')
}
const CLIENT = makeClient()
const CLIENT_GATES = {
  S0: { approved: '2026-10-05' },
  S1: { approved: '2026-10-20', signed_by: 'J. Smith, Operations Director', rules_sha256: 'a'.repeat(64), rules_md_sha256: 'b'.repeat(64), deposit_received: true },
  S2: { approved: '2026-11-03' }, S3: { approved: '2026-11-10' }, S4: { approved: '2026-11-20' }, S5: { approved: '2026-12-04' },
}
function clientArgs(stage_id, extra = {}) {
  return {
    pipeline: PIPE, engagement: clone(CLIENT), stage_id, prepared: stage_id, host_attested: true, gates: clone(CLIENT_GATES),
    paths: { pipeline: PIPELINE_DIR, target_repo: CLIENT_REPO, target: CLIENT_REPO, private: `${HOME}/engagements/${CLIENT_ID}/private`, records: CLIENT_RECORDS, env_file: ENV_FILE },
    ...extra,
  }
}
const fanoutTask = (stageId, id) => (PIPE.stages.find(s => s.id === stageId) || { tasks: [] }).tasks.find(t => t.id === id)
const S5_FANOUT = (PIPE.stages.find(s => s.id === 'S5') || { tasks: [] }).tasks.find(t => t.kind === 'fanout')
const S1_FANOUT = (PIPE.stages.find(s => s.id === 'S1') || { tasks: [] }).tasks.find(t => t.kind === 'fanout')
const itemsFor = engId => {
  const hint = (ENG[engId] && ENG[engId].scope && ENG[engId].scope.areas_hint) || []
  const ids = hint.length ? hint : ['orders', 'billing', 'stock']
  return ids.slice(0, (S5_FANOUT && S5_FANOUT.max_items) || 4).map(id => ({ id, title: id.replace(/_/g, ' '), scope: `rules: R-${id}-001` }))
}
const unfilled = r => [...new Set(r.calls.flatMap(c => c.prompt.match(/\{\{[a-z_]+(?:\.\w+)+\}\}/g) || []))]
const taskStatus = (r, id) => { const t = ((r.result || {}).tasks || []).find(x => x.id === id); return t && t.status }
const problemsOf = r => (r.result && r.result.problems) || []

// ---------- command forms (driver header; decisions L1, L3.2) ----------
// Splits a command into words the way the client Bash hook (shlex, punctuation_chars, posix) sees it,
// and throws on anything the hook blocks: an operator or $ outside quotes, a newline, an unbalanced quote.
function hookWords(s) {
  const words = []
  let cur = null
  for (let i = 0; i < s.length;) {
    const c = s[i]
    if (c === "'") { const j = s.indexOf("'", i + 1); if (j < 0) throw new Error('unbalanced single quote'); cur = (cur || '') + s.slice(i + 1, j); i = j + 1 } else if (c === '"') {
      let j = i + 1; let v = ''
      for (; j < s.length && s[j] !== '"'; j++) { if (s[j] === '$' || s[j] === '`') throw new Error('expansion in double quotes'); if (s[j] === '\\' && '"\\'.includes(s[j + 1])) j++; v += s[j] }
      if (j >= s.length) throw new Error('unbalanced double quote')
      cur = (cur || '') + v; i = j + 1
    } else if (c === '\\') { cur = (cur || '') + (s[i + 1] || ''); i += 2 } else if (c === ' ' || c === '\t') { if (cur !== null) words.push(cur); cur = null; i++ } else if (/[;&|<>()`$\n]/.test(c)) throw new Error(`"${c}" outside quotes`)
    else { cur = (cur || '') + c; i++ }
  }
  if (cur !== null) words.push(cur)
  return words
}
const labelSlot = l => String(l).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
const bracedHead = slot => `. ${ENV_FILE} && export MP_DB_SUFFIX=${slot} && `
// Returns { form: data-plane|mp-run|braced, slot, inner?, words? } or { form: 'other', why }.
function formOf(cmd, engFile) {
  if (cmd.startsWith('mp-data ')) {
    try {
      const w = hookWords(cmd)
      if (w[1] !== '--slot' || !w[2]) return { form: 'other', why: 'data-plane call without --slot <slot> first' }
      return { form: 'data-plane', slot: w[2], words: w }
    } catch (e) { return { form: 'other', why: `data-plane call the hook would block: ${e.message}` } }
  }
  if (cmd.startsWith('mp-run ')) {
    try {
      const w = hookWords(cmd)
      if (w.length !== 7 || w[1] !== '--eng' || w[3] !== '--slot' || w[5] !== '--') return { form: 'other', why: `mp-run words ${JSON.stringify(w.slice(0, 6))}` }
      if (engFile && w[2] !== engFile) return { form: 'other', why: `mp-run --eng ${w[2]}, want ${engFile}` }
      return { form: 'mp-run', slot: w[4], inner: w[6], words: w }
    } catch (e) { return { form: 'other', why: `mp-run line the hook would block: ${e.message}` } }
  }
  const m = /^\. (\S+) && export MP_DB_SUFFIX=(\S+) && (?:cd \S+ && )?\{ ([\s\S]*); \}$/.exec(cmd)
  if (m && m[1] === ENV_FILE) return { form: 'braced', slot: m[2], inner: m[3] }
  return { form: 'other', why: 'not one of the three anchored forms' }
}
// Every anchored command an agent was given in a schema enum (acceptance, integrate checks, checkpoint checks).
const enumCmds = c => ['acceptance', 'exit_criteria', 'checks'].flatMap(k => (c.opts.schema && c.opts.schema.properties && c.opts.schema.properties[k] ? enumOf(c.opts, k) : []))

// ---------- dry runs: every stage, both demos and a filled client ----------
head('Dry runs: no problems for the demos and a filled client')
if (autoFilled.length) console.log(`  note: the client fixture auto-filled template markers at ${autoFilled.join(', ')}`)
const table = []
await block('dry runs: every stage, both demos and a filled client', async () => { for (const st of STAGES) {
  const row = { stage: st, budget: PIPE.stages.find(s => s.id === st).agent_budget }
  for (const id of ['northwind-demo', 'invoicegen-demo']) {
    const items = S5_FANOUT && st === 'S5' ? { [S5_FANOUT.id]: itemsFor(id) } : undefined
    const d = await runDriver(demoArgs(id, st, { dry_run: true, items }))
    check(`${id} ${st} dry run returns`, !!d.result && !d.error, d.error)
    check(`${id} ${st} dry run has no problems`, problemsOf(d).length === 0, problemsOf(d).join('\n'))
    check(`${id} ${st} dry run spawns no agent`, d.calls.length === 0)
    if (d.result && d.result.warnings && d.result.warnings.length) console.log(`  warnings ${id} ${st}:\n    - ${d.result.warnings.join('\n    - ')}`)
    row[id] = d.result && d.result.not_applicable ? 'n/a' : d.result && d.result.estimate ? `${d.result.estimate.typical}/${d.result.estimate.worst}` : '?'
    if (d.result && !d.result.not_applicable) {
      check(`${id} ${st} dry run lists preflight ids`, Array.isArray(d.result.preflight) && d.result.preflight.every(x => typeof x === 'string'))
      const prepBad = (d.result.prepare || []).filter(c => { const f = formOf(c, `${PIPELINE_DIR}/engagements/${id}/engagement.json`); return f.slot !== `${st.toLowerCase()}_prepare` || f.form === 'other' || (f.form === 'braced' && st !== 'S0') || (f.form === 'mp-run' && st === 'S0') })
      check(`${id} ${st} prepare commands are anchored with slot ${st.toLowerCase()}_prepare (data-plane unwrapped; braced only in S0 bootstrap)`, prepBad.length === 0, prepBad[0])
      check(`${id} ${st} gate kind is demo`, d.result.gate && d.result.gate.kind === 'demo' && d.result.gate.checklist.length > 0)
      check(`${id} ${st} branches are filled`, d.result.branches && !/\{\{/.test(JSON.stringify(d.result.branches)))
    }
  }
  const c = await runDriver(clientArgs(st, { dry_run: true, items: S5_FANOUT && st === 'S5' ? { [S5_FANOUT.id]: [{ id: 'orders', title: 'Orders', scope: 'rules: R-orders-001' }, { id: 'invoicing', title: 'Invoicing', scope: 'rules: R-invoicing-001' }] } : undefined }))
  check(`client ${st} dry run has no problems`, !c.error && problemsOf(c).length === 0, c.error || problemsOf(c).join('\n'))
  if (c.result && !c.result.not_applicable) check(`client ${st} gate kind is client`, c.result.gate && c.result.gate.kind === 'client')
  row.client = c.result && c.result.not_applicable ? 'n/a' : c.result && c.result.estimate ? `${c.result.estimate.typical}/${c.result.estimate.worst}` : '?'
  table.push(row)
} })

// ---------- launcher (make-launcher.py) ----------
// pipeline.json is too large to pass inline, so the orchestrator runs make-launcher.py, which writes a launcher
// with pipeline.json sliced (the run stage in full, every other stage as a stub), engagement.json and gates.json
// embedded, and calls Workflow on it. The launcher calls the driver with workflow({ scriptPath }, args). Here the
// mocked workflow() runs the driver on those args; the dry run must equal the one with the full pipeline.json.
head('Launcher: make-launcher.py gives the same dry run as the full pipeline.json')
await block('launcher (make-launcher.py)', async () => {
  const LIMIT = 500000
  const NOT_IN_ARGS = ['pipeline', 'engagement', 'gates', 'prior', 'prior_items', 'stage_id']
  const argsFileOf = a => Object.fromEntries(Object.entries(a).filter(([k, v]) => !NOT_IN_ARGS.includes(k) && v !== undefined))
  const py = spawnSync('python3', ['-I', '-c', 'pass'], { stdio: 'ignore' })
  if (py.error || py.status !== 0) { console.log('  note: python3 not found, so make-launcher.py was not run'); return }
  check('make-launcher.py is found next to the driver (or at MAKE_LAUNCHER)', fs.existsSync(MAKE_LAUNCHER), MAKE_LAUNCHER)
  if (!fs.existsSync(MAKE_LAUNCHER)) return
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mp-launcher-'))
  try {
    // make-launcher.py reads <pipeline>/pipeline.json; PIPELINE_JSON may name a merged file with another name.
    let pipeDir = path.dirname(path.resolve(PIPELINE_JSON))
    if (path.basename(PIPELINE_JSON) !== 'pipeline.json') { pipeDir = path.join(tmp, 'pipeline'); fs.mkdirSync(pipeDir); fs.copyFileSync(PIPELINE_JSON, path.join(pipeDir, 'pipeline.json')) }
    const driverPath = path.resolve(DRIVER)
    const makeLauncher = (rec, st, argsFile, out) => spawnSync('python3', ['-I', path.resolve(MAKE_LAUNCHER), '--pipeline', pipeDir, '--records', rec, '--stage', st, '--args', argsFile, '--out', out, '--driver', driverPath], { encoding: 'utf8' })
    // One records dir per engagement: engagement.json, and gates.json for the client.
    const recordsFor = (who, a) => {
      const rec = path.join(tmp, who)
      if (!fs.existsSync(rec)) {
        fs.mkdirSync(rec)
        fs.writeFileSync(path.join(rec, 'engagement.json'), JSON.stringify(a.engagement, null, 2))
        if (a.gates) fs.writeFileSync(path.join(rec, 'gates.json'), JSON.stringify(a.gates, null, 2))
      }
      return rec
    }
    const s5Items = who => (S5_FANOUT ? { [S5_FANOUT.id]: who === 'client' ? [{ id: 'orders', title: 'Orders', scope: 'rules: R-orders-001' }, { id: 'invoicing', title: 'Invoicing', scope: 'rules: R-invoicing-001' }] : itemsFor(who) } : undefined)
    const clientStages = PIPE.stages.filter(s => !s.applies_to || s.applies_to.includes(CLIENT.source.kind)).map(s => s.id)
    const cases = [
      ...['northwind-demo', 'invoicegen-demo'].flatMap(id => STAGES.map(st => [id, st, demoArgs(id, st, { dry_run: true, items: st === 'S5' ? s5Items(id) : undefined })])),
      ...clientStages.map(st => ['client', st, clientArgs(st, { dry_run: true, items: st === 'S5' ? s5Items('client') : undefined })]),
    ]
    let largest = 0
    const notSliced = []
    for (const [who, st, a] of cases) {
      const rec = recordsFor(who, a)
      const argsFile = path.join(tmp, `args-${who}-${st}.json`)
      fs.writeFileSync(argsFile, JSON.stringify(argsFileOf(a)))
      const out = path.join(rec, `launch-${st}.workflow.js`)
      const ml = makeLauncher(rec, st, argsFile, out)
      check(`${who} ${st}: make-launcher.py writes the launcher`, ml.status === 0 && fs.existsSync(out), ml.error ? ml.error.message : `exit ${ml.status}: ${ml.stderr}`)
      if (ml.status !== 0 || !fs.existsSync(out)) continue
      const size = fs.statSync(out).size
      largest = Math.max(largest, size)
      check(`${who} ${st}: the launcher is under ${LIMIT} bytes`, size < LIMIT, `${size} bytes`)
      const full = await runDriver(a)
      let ref = null
      let via = null
      const res = await new AsyncFunction('workflow', fs.readFileSync(out, 'utf8').replace(/^export const meta/m, 'const meta'))(async (r, la) => {
        ref = r
        if (la.pipeline.stages.some(s => s.id !== st && s.tasks)) notSliced.push(`${who} ${st}`)
        via = await runDriver(la)
        return via.result
      })
      const differ = full.result && res ? [...new Set([...Object.keys(full.result), ...Object.keys(res)])].filter(k => !isDeepStrictEqual(full.result[k], res[k])) : []
      check(`${who} ${st}: the launcher's dry run equals the dry run with the full pipeline.json`, !!full.result && !full.error && !!via && !via.error && via.calls.length === 0 && isDeepStrictEqual(res, full.result), full.error || (via && via.error) || `differs at ${differ.join(', ') || '(no result)'}`)
      check(`${who} ${st}: the launcher calls workflow() with the driver's scriptPath`, !!ref && ref.scriptPath === driverPath, ref)
    }
    check('every launcher embeds the run stage in full and every other stage as a stub (no tasks)', notSliced.length === 0, notSliced)
    console.log(`  note: ${cases.length} launchers, largest ${largest} bytes (limit ${LIMIT})`)
    // The orchestrator must not put file contents in the args file; make-launcher.py reads them from disk.
    const nwArgs = demoArgs('northwind-demo', 'S2', { dry_run: true })
    const badArgs = path.join(tmp, 'args-with-pipeline.json')
    fs.writeFileSync(badArgs, JSON.stringify({ ...argsFileOf(nwArgs), pipeline: PIPE }))
    const badOut = path.join(tmp, 'launch-refused.workflow.js')
    const bad = makeLauncher(recordsFor('northwind-demo', nwArgs), 'S2', badArgs, badOut)
    check('make-launcher.py exits 2 and writes no launcher when the args file holds pipeline', bad.status === 2 && !fs.existsSync(badOut) && /pipeline/.test(bad.stderr), `exit ${bad.status}: ${bad.stderr}`)
  } finally { fs.rmSync(tmp, { recursive: true, force: true }) }
})

// ---------- all-pass runs with mocked agents ----------
head('All-pass runs: ready, agent count equals the estimate, clean prompts')
const allPass = {}
await block('all-pass runs with mocked agents', async () => { for (const id of ['northwind-demo', 'invoicegen-demo']) {
  for (const st of STAGES) {
    const items = S5_FANOUT && st === 'S5' ? { [S5_FANOUT.id]: itemsFor(id) } : undefined
    const d = await runDriver(demoArgs(id, st, { dry_run: true, items }))
    const r = await runDriver(demoArgs(id, st, { items, commit_trailer: 'Co-Authored-By: Test <t@example.com>' }))
    allPass[`${id} ${st}`] = r
    if (r.result && r.result.not_applicable) { check(`${id} ${st} not applicable, ready, no agents`, r.result.ready === true && r.calls.length === 0); continue }
    check(`${id} ${st} runs`, !r.error, r.error)
    check(`${id} ${st} ready`, r.result && r.result.ready === true, r.result && r.result.blockers)
    check(`${id} ${st} agents spawned equal the typical estimate`, r.result && d.result && r.calls.length === d.result.estimate.typical && r.result.agents.spawned === r.calls.length, `spawned ${r.calls.length}, estimate ${d.result && d.result.estimate.typical}`)
    check(`${id} ${st} no unfilled placeholder reaches an agent`, unfilled(r).length === 0, unfilled(r).join(' '))
    check(`${id} ${st} no two concurrent agents own the same path`, r.overlaps.length === 0, r.overlaps.slice(0, 5).join('\n'))
    check(`${id} ${st} every agent label is unique`, new Set(r.labels).size === r.labels.length, r.labels.join(' '))
    check(`${id} ${st} every phase is in meta.phases`, r.phases.every(p => PHASES.has(p)), [...new Set(r.phases)].join(', '))
    check(`${id} ${st} no checkpoint on a ready run`, !r.labels.some(l => /checkpoint/.test(l)))
    const integ = r.calls.find(c => /:integrate$/.test(c.label))
    check(`${id} ${st} integrate prompt has the log row with the agent count`, integ && integ.prompt.includes(`| ${r.calls.length} | ${d.result.estimate.typical}/${d.result.estimate.worst} |`))
    check(`${id} ${st} integrate prompt carries the commit trailer`, integ && integ.prompt.includes('Co-Authored-By: Test <t@example.com>'))
    check(`${id} ${st} report has results, gate and agents`, r.result && r.result.results && r.result.gate && r.result.gate.kind === 'demo' && r.result.agents && r.result.agents.output_tokens_turn === 4242)
  }
} })
await block('all-pass runs with mocked agents', async () => {
  const r = allPass['northwind-demo S0']
  const ws = r && r.calls.find(c => c.label === 'S0.workspace')
  const acc = ws ? enumOf(ws.opts, 'acceptance') : []
  const shell = acc.filter(c => !c.startsWith('mp-data '))
  check('S0 bootstrap: every non-data-plane acceptance is braced (env file, MP_DB_SUFFIX slot, cd to the pipeline)', shell.length > 0 && shell.every(c => c.startsWith(bracedHead('s0_workspace')) && c.endsWith('; }')), shell[0])
  check('S0 bootstrap: data-plane calls stay unwrapped with --slot', acc.filter(c => c.startsWith('mp-data ')).every(c => c.startsWith('mp-data --slot s0_workspace ')))
  const cdCmd = shell.find(c => /MP_DB_SUFFIX=s0_workspace && \{ cd /.test(c))
  const plain = shell.find(c => c.includes(`&& cd ${PIPELINE_DIR} && { `))
  check('a command that starts with cd is not prefixed with cd <pipeline>', !cdCmd || !cdCmd.includes(`cd ${PIPELINE_DIR} && { cd `))
  check('a plain command is anchored to the pipeline checkout', !!plain)
  const rv = r && r.calls.find(c => c.label === 'S0.review:r1')
  check('reviewer slot is s0_review_r1 and its commands use it', rv && rv.prompt.includes('MP_DB_SUFFIX=s0_review_r1 && '))
  const integ = r && r.calls.find(c => c.label === 'S0:integrate')
  check('integrate checks the staged files with the guard and pre-commit', integ && /mp-data --slot s0_integrate guard \S+\/engagement\.json --repo \S+ --repo \S+ --staged/.test(integ.prompt) && integ.prompt.includes('pre-commit run'))
  check('integrate stages owned paths only, never private', integ && integ.prompt.includes(`git -C ${PIPELINE_DIR} add -A -- '<path>'`) && !/^- sources\/$/m.test(integ.prompt))
})
await block('all-pass runs with mocked agents', async () => {
  const r = allPass['northwind-demo S5']
  const items = r ? r.calls.filter(c => /^S5\.feature\./.test(c.label)) : []
  const slots = items.map(c => (c.prompt.match(/Your MP_DB_SUFFIX slot: (\S+)\./) || [])[1])
  check('S5 feature agents get distinct MP_DB_SUFFIX slots', items.length > 1 && new Set(slots).size === slots.length, slots.join(' '))
  const reuse = (S5_FANOUT && S5_FANOUT.reuse) || []
  check('fan-out items get the fan-out\'s own reuse list and bootstrap build spec', items.length > 0 && items.every(c => (!reuse.length || c.prompt.includes('Use these existing tools')) && (!(S5_FANOUT.bootstrap && S5_FANOUT.bootstrap.build) || c.prompt.includes('Build spec (bootstrap):'))))
  check('slots are at most 24 characters', r && r.calls.every(c => [...c.prompt.matchAll(/(?:MP_DB_SUFFIX=|--slot )([a-z0-9_]+)/g)].every(m => m[1].length <= 24)))
})

// ---------- staging plan replay (walk:demo-later/F1) ----------
// The integrate and checkpoint prompts stage with `git add -A -- <path>` per owned path, unstage owns_except
// paths with `git reset -q -- <path>`, then stage again the excluded paths another done task owns. The replay
// runs those three lists in order on one sample file per path (git pathspec semantics: a path also matches
// everything under it, * matches any characters).
function stagingLists(prompt) {
  const part = String(prompt).split('\n\n').find(p => p.startsWith('Stage only'))
  const repos = {}
  let cur = null, mode = null
  for (const line of (part || '').split('\n')) {
    const m = /^(\w+) repo \((\S+)\): run `git -C \S+ add -A -- '<path>'`/.exec(line)
    if (m) { cur = repos[m[1]] = { dir: m[2], add: [], reset: [], readd: [] }; mode = 'add'; continue }
    if (!cur) continue
    if (line.startsWith('then unstage these')) mode = 'reset'
    else if (line.startsWith('then stage these again')) mode = 'readd'
    else if (line.startsWith('- ')) cur[mode].push(line.slice(2))
  }
  return repos
}
const specRe = s => new RegExp(`^${s.replace(/[.+^$()|[\]\\{}?]/g, '\\$&').replace(/\*/g, '.*')}${s.endsWith('/') ? '.*' : '(/.*)?'}$`)
const sampleOf = p => (p.endsWith('/') ? `${p}sample.txt` : p).replace(/\*/g, '1')
function replayStaging(lists, extraFiles) {
  const files = new Set([...lists.add, ...lists.reset, ...lists.readd, ...(extraFiles || [])].map(sampleOf))
  const staged = new Set()
  for (const p of lists.add) for (const f of files) if (specRe(p).test(f)) staged.add(f)
  for (const p of lists.reset) for (const f of files) if (specRe(p).test(f)) staged.delete(f)
  for (const p of lists.readd) for (const f of files) if (specRe(p).test(f)) staged.add(f)
  return staged
}
// Owned and excluded paths of a stage's tasks, straight from pipeline.json, as [repo, repo-relative path].
function ownedOf(engId, stageId, which, only) {
  const eng = ENG[engId]
  const troot = eng.target && eng.target.root && eng.target.root !== '.' ? `${eng.target.root}/` : ''
  const fillE = s => s.replace(/\{\{e\.([\w.]+)\}\}/g, (m, k) => { const v = k.split('.').reduce((o, x) => (o == null ? undefined : o[x]), eng); return v == null ? m : String(v) })
  const rel = o => {
    const i = o.indexOf(':'); const root = o.slice(0, i); const rest = fillE(o.slice(i + 1))
    if (root === 'pipeline') return ['pipeline', rest]
    if (root === 'target') return ['target', `${troot}${rest}`]
    if (root === 'target-repo') return ['target', rest]
    if (root === 'records') return ['pipeline', `engagements/${engId}/${rest}`]
    return null
  }
  const tasks = (PIPE.stages.find(s => s.id === stageId) || { tasks: [] }).tasks
    .filter(t => t.kind === 'build' && (!t.applies_to || t.applies_to.includes(eng.source.kind)) && (!only || only.includes(t.id)))
  return tasks.flatMap(t => (t[which] || []).map(rel).filter(Boolean))
}
await block('staging plan replay (walk:demo-later/F1)', async () => {
  for (const id of ['northwind-demo', 'invoicegen-demo']) {
    for (const st of ['S2', 'S4']) {
      const r = allPass[`${id} ${st}`]
      const integ = r && r.calls.find(c => c.label === `${st}:integrate`)
      const lists = integ ? stagingLists(integ.prompt) : {}
      const owns = ownedOf(id, st, 'owns')
      const excepts = ownedOf(id, st, 'owns_except')
      check(`${id} ${st}: the integrate prompt has a staging list per repo`, !!lists.pipeline && !!lists.target, Object.keys(lists))
      if (!lists.pipeline || !lists.target) continue
      const staged = Object.fromEntries(Object.entries(lists).map(([k, l]) => [k, replayStaging(l, [...owns, ...excepts].filter(x => x[0] === k).map(x => x[1]))]))
      const lost = owns.filter(([k, p]) => !(staged[k] && staged[k].has(sampleOf(p)))).map(x => x.join(':'))
      check(`${id} ${st}: after add, reset and re-add, every owned path of every done task is staged`, owns.length > 0 && lost.length === 0, lost)
      const ownedExactly = new Set(owns.map(x => x.join(':')))
      const free = excepts.filter(x => !ownedExactly.has(x.join(':')))
      const leaked = free.filter(([k, p]) => staged[k] && staged[k].has(sampleOf(p))).map(x => x.join(':'))
      check(`${id} ${st}: an owns_except path that no done task owns stays unstaged (${free.length} such paths)`, leaked.length === 0, leaked)
      const readd = Object.entries(lists).flatMap(([k, l]) => l.readd.map(p => `${k}:${p}`))
      const wantReadd = owns.filter(([k, p]) => excepts.some(([ek, e]) => ek === k && (p === e || p.startsWith(e)))).map(x => x.join(':'))
      check(`${id} ${st}: the re-add list is exactly the excluded paths another done task owns`, JSON.stringify([...new Set(readd)].sort()) === JSON.stringify([...new Set(wantReadd)].sort()), `re-add ${readd.join(' ')}\nwant  ${wantReadd.join(' ')}`)
    }
  }
  // The paths named in the finding, for northwind-demo.
  const nwStaged = st => { const integ = (allPass[`northwind-demo ${st}`] || { calls: [] }).calls.find(c => c.label === `${st}:integrate`); const l = integ ? stagingLists(integ.prompt) : {}; return k => (l[k] ? replayStaging(l[k], ['northwind/app/northwind/domain/']) : new Set()) }
  const s4 = nwStaged('S4'); const s2 = nwStaged('S2')
  check('northwind-demo S4: legacy/ and core/ of the app and templates/django-overlay/core/ end staged', ['northwind/app/northwind/legacy/', 'northwind/app/northwind/core/'].every(p => s4('target').has(sampleOf(p))) && s4('pipeline').has(sampleOf('templates/django-overlay/core/')))
  check('northwind-demo S4: northwind/app/northwind/domain/ (owns_except, no S4 owner) stays unstaged', s4('target').has(sampleOf('northwind/app/')) && !s4('target').has(sampleOf('northwind/app/northwind/domain/')))
  check('northwind-demo S2: harness/canonical.py and harness/tests/test_canonical.py end staged', ['harness/canonical.py', 'harness/tests/test_canonical.py'].every(p => s2('pipeline').has(p)))
  // The checkpoint uses the same plan: with S4.data blocked, S4.models' legacy/ is staged again and S4.data's core/ is not.
  const cp = (await runDriver(demoArgs('northwind-demo', 'S4'), { onAgent: (p, o) => (o.label === 'S4.data' ? { status: 'blocked', summary: 'needs a manual step', files_changed: [], acceptance: [] } : undefined) })).calls.find(c => c.label === 'S4:checkpoint')
  const cl = cp ? stagingLists(cp.prompt) : {}
  const ct = cl.target ? replayStaging(cl.target, ['northwind/app/northwind/core/']) : new Set()
  const cpp = cl.pipeline ? replayStaging(cl.pipeline, ['templates/django-overlay/core/']) : new Set()
  check('checkpoint (S4.data blocked): legacy/ staged again; core/ and templates/django-overlay/core/ stay unstaged', ct.has(sampleOf('northwind/app/northwind/legacy/')) && !ct.has(sampleOf('northwind/app/northwind/core/')) && !cpp.has(sampleOf('templates/django-overlay/core/')), cl)
})

// ---------- the client engagement: real runs ----------
head('Client engagement (from _template)')
await block('the client engagement: real runs', async () => { for (const st of ['S0', 'S2', 'S5']) {
  const items = S5_FANOUT && st === 'S5' ? { [S5_FANOUT.id]: [{ id: 'orders', title: 'Orders', scope: 'rules: R-orders-001' }, { id: 'invoicing', title: 'Invoicing', scope: 'rules: R-invoicing-001' }] } : undefined
  const r = await runDriver(clientArgs(st, { items }))
  check(`client ${st} ready`, !r.error && r.result && r.result.ready === true, r.error || (r.result && r.result.blockers))
  const integ = r.calls.find(c => /:integrate$/.test(c.label))
  check(`client ${st} integrate commits the separate records repo`, integ && integ.prompt.includes(`Commit the records repo ${CLIENT_RECORDS}`))
  check(`client ${st} prompts do not carry the client's name`, r.calls.every(c => !c.prompt.includes('Acme Widgets Ltd')))
  if (st === 'S0') check('client S0 exit criteria include the data-zone read-denial check', integ && integ.prompt.includes(`cat ${HOME}/engagements/${CLIENT_ID}/private/sources/*`))
  const repoLess = await runDriver(clientArgs(st, { items }), { onAgent: (p, o) => (isIntegrate(o) ? { exit_criteria: enumOf(o, 'exit_criteria').map(cmd => ({ cmd, exit_code: 0, passed: true })), repos: [{ repo: 'pipeline', branch: fillBranch(PIPE.branches.pipeline, CLIENT, PIPE.stages.find(s => s.id === st)), commit: 'a1b2c3d', pushed: true }, { repo: 'target', branch: fillBranch(PIPE.branches.target, CLIENT, PIPE.stages.find(s => s.id === st)), commit: 'b2c3d4e', pushed: true }], unowned_changes: [] } : undefined) })
  check(`client ${st} not ready when the records repo is not committed`, repoLess.result && repoLess.result.ready === false)
} })
await block('the client engagement: real runs', async () => {
  const local = clone(CLIENT); local.target = { ...local.target, mode: 'local', repo: null, local_path: CLIENT_REPO }
  local.commercial = { ...local.commercial, tier: 'assessment', purchased: ['assessment'] }
  const r = await runDriver({ ...clientArgs('S1', { items: S1_FANOUT ? { [S1_FANOUT.id]: [{ id: 'orders', title: 'Orders', scope: 'form: Orders' }] } : undefined }), engagement: local }, {
    onAgent: (p, o) => (isIntegrate(o) ? { exit_criteria: enumOf(o, 'exit_criteria').map(cmd => ({ cmd, exit_code: 0, passed: true })), repos: [{ repo: 'pipeline', branch: `eng/${CLIENT_ID}/S1-${PIPE.stages[1].slug}`, commit: 'a1b2c3d', pushed: true }, { repo: 'target', branch: `${CLIENT_ID}/S1-${PIPE.stages[1].slug}`, commit: 'b2c3d4e', pushed: false }, { repo: 'records', branch: 'main', commit: 'c3d4e5f', pushed: false }], unowned_changes: [] } : undefined),
  })
  check('target.mode local: ready without pushing the target repo', !r.error && r.result && r.result.ready === true, r.error || (r.result && r.result.blockers))
  const integ = r.calls.find(c => /:integrate$/.test(c.label))
  check('target.mode local: integrate is told not to push the target', integ && integ.prompt.includes('never push it'))
})

// ---------- the unedited template is refused ----------
head('Unedited _template')
await block('the unedited template is refused', async () => {
  const a = clientArgs('S0', { dry_run: true }); a.engagement = clone(ENG._template); a.paths.records = CLIENT_RECORDS
  const d = await runDriver(a)
  const ps = problemsOf(d)
  check('unedited _template S0 dry run lists problems', ps.length > 0)
  check('problems name the id, the source kind and template markers', ps.some(p => /engagement\.id/.test(p)) && ps.some(p => /source\.kind/.test(p)) && ps.some(p => /template marker/.test(p)), ps.slice(0, 8).join('\n'))
  const a3 = clientArgs('S3', { dry_run: true }); a3.engagement = clone(ENG._template)
  const d3 = await runDriver(a3)
  check('unedited _template S3 is not "not applicable, ready"', d3.result && !d3.result.not_applicable && problemsOf(d3).length > 0)
  const real = await runDriver({ ...a, dry_run: false })
  check('unedited _template S0 real run throws and spawns nothing', !!real.error && real.calls.length === 0, real.error)
})

// ---------- scenarios ----------
head('Scenarios')
const nw = (st, extra) => demoArgs('northwind-demo', st, extra)
const okRepos = (o, a) => {
  const stage = PIPE.stages.find(s => s.id === a.stage_id)
  return [{ repo: 'pipeline', branch: fillBranch(PIPE.branches.pipeline, a.engagement, stage), commit: 'a1b2c3d', pushed: true }, { repo: 'target', branch: fillBranch(PIPE.branches.target, a.engagement, stage), commit: 'b2c3d4e', pushed: true }]
}

// Missing or wrong acceptance reports.
await block('Missing or wrong acceptance reports', async () => { for (const [name, accept] of [['reports no acceptance checks', () => []], ['reports an unrelated passing command', () => [{ cmd: 'uv run pytest -q', exit_code: 0, passed: true }]], ['reports a failing command as passed', o => enumOf(o, 'acceptance').map((cmd, i) => ({ cmd, exit_code: i === 0 ? 1 : 0, passed: true }))]]) {
  const r = await runDriver(nw('S0'), { onAgent: (p, o) => (o.label === 'S0.guard' ? { status: 'done', summary: 'ok', files_changed: [], acceptance: accept(o) } : undefined) })
  check(`S0.guard ${name}: blocked, stage not ready`, taskStatus(r, 'S0.guard') === 'blocked' && r.result.ready === false, r.result && r.result.tasks)
  check(`S0.guard ${name}: review skipped, checkpoint ran`, taskStatus(r, 'S0.review') === 'skipped' && r.labels.includes('S0:checkpoint') && !!r.result.checkpoint)
} })

// Integrate reports no exit criteria / wrong branch / unowned changes.
await block('Integrate reports no exit criteria / wrong branch / unowned changes', async () => {
  const a = nw('S3')
  const r = await runDriver(a, { onAgent: (p, o) => (isIntegrate(o) ? { exit_criteria: [], repos: okRepos(o, a), unowned_changes: [] } : undefined) })
  check('integrate with empty exit_criteria: not ready', r.result && r.result.ready === false && r.result.exit_missing.length > 0, r.result && r.result.blockers)
  check('integrate with empty exit_criteria: checkpoint ran', r.labels.includes('S3:checkpoint'))
  const w = await runDriver(a, { onAgent: (p, o) => (isIntegrate(o) ? { exit_criteria: enumOf(o, 'exit_criteria').map(cmd => ({ cmd, exit_code: 0, passed: true })), repos: okRepos(o, a).map(x => ({ ...x, branch: 'main' })), unowned_changes: [] } : undefined) })
  check('integrate on the wrong branch: not ready', w.result && w.result.ready === false)
  const u = await runDriver(a, { onAgent: (p, o) => (isIntegrate(o) ? { exit_criteria: enumOf(o, 'exit_criteria').map(cmd => ({ cmd, exit_code: 0, passed: true })), repos: okRepos(o, a), unowned_changes: ['app/.env'] } : undefined) })
  check('integrate with unowned changes: not ready', u.result && u.result.ready === false && u.result.blockers.some(b => /unowned/.test(b)))
  const f = await runDriver(a, { onAgent: (p, o) => (isIntegrate(o) ? { exit_criteria: enumOf(o, 'exit_criteria').map((cmd, i) => ({ cmd, exit_code: i === 0 ? 2 : 0, passed: i !== 0 })), repos: okRepos(o, a), unowned_changes: [] } : undefined) })
  check('integrate with a failed check: not ready, exit_failed lists it', f.result && f.result.ready === false && f.result.exit_failed.length === 1)
})

// Blocking finding on a task that is not a direct dependency of the review: fixed with its descendants.
await block('Blocking finding on a task that is not a direct dependency of the revi', async () => {
  let first = true
  const r = await runDriver(nw('S0'), {
    onAgent: (p, o) => {
      if (isVerdict(o) && o.label === 'S0.review:r1' && first) { first = false; return { issues: [{ task_id: 'S0.parse', severity: 'blocking', problem: 'CRLF modules lose their last line', evidence: 'pytest toolkit/tests/test_parse_vba.py exit 1' }] } }
      return undefined
    },
  })
  const rv = r.calls.find(c => c.label === 'S0.review:r1')
  const ids = rv ? rv.opts.schema.properties.issues.items.properties.task_id.enum : []
  check('first-pass reviewer sees every build task of the stage as a card', rv && ['S0.workspace', 'S0.parse', 'S0.guard', 'S0.healthcheck'].every(id => rv.prompt.includes(`### ${id} `)) && ids.includes('S0.parse'), ids)
  check('reviewer prompt lists the exit criteria and the evidence rule', rv && rv.prompt.includes('Stage exit criteria') && rv.prompt.includes('If you cannot find evidence that a task is done, report a blocking issue against that task'))
  const fixes = r.labels.filter(l => /:fix1$/.test(l))
  const pos = l => r.labels.indexOf(l)
  const s0 = PIPE.stages.find(s => s.id === 'S0').tasks.filter(t => t.kind === 'build')
  const desc = new Set(['S0.parse'])
  for (let grew = true; grew;) { grew = false; for (const t of s0) if (!desc.has(t.id) && (t.depends_on || []).some(d => desc.has(d))) { desc.add(t.id); grew = true } }
  const want = [...desc].map(id => `${id}:fix1`)
  check('fix round re-runs S0.parse and exactly the build tasks downstream of it', want.length > 1 && want.every(l => fixes.includes(l)) && fixes.length === want.length, `want ${want.join(' ')}; got ${fixes.join(' ')}`)
  const depOrder = s0.filter(t => desc.has(t.id)).every(t => (t.depends_on || []).filter(d => desc.has(d)).every(d => pos(`${d}:fix1`) < pos(`${t.id}:fix1`)))
  check('fix round runs in dependency order', depOrder, r.labels.join(' '))
  const down = r.calls.find(c => c.label === 'S0.healthcheck:fix1')
  check('descendants are told that upstream changed', down && down.prompt.includes('Upstream S0.parse changed in fix round 1'))
  check('re-review ran and the stage is ready', r.labels.includes('S0.review:r1:round1') && r.result.ready === true && r.result.fix_rounds_used === 1, r.result && r.result.blockers)
})

// Blocking finding on a fan-out item: the item and the synthesis after it are redone.
await block('Blocking finding on a fan-out item: the item and the synthesis after i', async () => { if (S1_FANOUT) {
  let first = true
  const r = await runDriver(nw('S1'), {
    onAgent: (p, o) => {
      const id = S1_FANOUT.each.id.replace('{{item.id}}', 'orders')
      if (isVerdict(o) && /:r1$/.test(o.label) && first) { first = false; return { issues: [{ task_id: id, severity: 'blocking', problem: 'misses the discount rule', evidence: 'rules/orders.json has no rule for Discount' }] } }
      return undefined
    },
  })
  const itemFix = `${S1_FANOUT.each.id.replace('{{item.id}}', 'orders')}:fix1`
  const down = r.labels.filter(l => /:fix1$/.test(l) && l !== itemFix)
  check('fan-out item finding: the item is fixed and downstream tasks redone', r.labels.includes(itemFix) && down.length > 0 && r.labels.indexOf(itemFix) < Math.min(...down.map(l => r.labels.indexOf(l))), r.labels.join(' '))
  check('fan-out item finding: other items are not redone', !r.labels.some(l => /inventory:fix1$/.test(l)))
} })

// A fix that comes back blocked skips the re-review.
await block('A fix that comes back blocked skips the re-review', async () => {
  let first = true
  const r = await runDriver(nw('S0'), {
    onAgent: (p, o) => {
      if (isVerdict(o) && o.label === 'S0.review:r1' && first) { first = false; return { issues: [{ task_id: 'S0.guard', severity: 'blocking', problem: 'misses a planted copy', evidence: 'test_guard.py exit 1' }] } }
      if (o.label === 'S0.guard:fix1') return { status: 'blocked', summary: 'needs a decision', files_changed: [], acceptance: [] }
      return undefined
    },
  })
  check('blocked fix: no re-review, not ready, checkpoint', !r.labels.some(l => /round1/.test(l)) && r.result.ready === false && r.labels.includes('S0:checkpoint'), r.labels.join(' '))
})

// Reviewer returns nothing; minor issues are reported.
await block('Reviewer returns nothing; minor issues are reported', async () => {
  const r = await runDriver(nw('S0'), { onAgent: (p, o) => (o.label === 'S0.review:r2' ? null : undefined) })
  check('a reviewer that returns null blocks the review and the stage', taskStatus(r, 'S0.review') === 'blocked' && r.result.ready === false)
  const m = await runDriver(nw('S0'), { onAgent: (p, o) => (o.label === 'S0.review:r2' ? { issues: [{ task_id: 'S0.guard', severity: 'minor', problem: 'threshold untested', evidence: 'no test sets noise_t' }] } : undefined) })
  check('minor issues are in the report and do not block', m.result.ready === true && m.result.issues.some(i => i.problem === 'threshold untested'))
})

// Fan-out item validation.
await block('Fan-out item validation', async () => { if (S5_FANOUT) {
  const F = S5_FANOUT.id
  const max = S5_FANOUT.max_items || 4
  const bad = [
    ['zero items', []],
    ['duplicate ids', [{ id: 'orders', title: 'A', scope: 'x' }, { id: 'orders', title: 'B', scope: 'y' }]],
    ['path-escaping and kebab-case ids', [{ id: '../../core', title: 'X', scope: 's' }, { id: 'order-entry', title: 'Y', scope: 's' }]],
    ['a reserved id', [{ id: 'core', title: 'Core', scope: 's' }]],
    ['the app slug as an id', [{ id: ENG['northwind-demo'].app.slug, title: 'App', scope: 's' }]],
    ['too many items', Array.from({ length: max + 1 }, (_, i) => ({ id: `area_${i}`, title: `Area ${i}`, scope: 's' }))],
  ]
  for (const [name, items] of bad) {
    const r = await runDriver(nw('S5', { items: { [F]: items } }))
    check(`${F} with ${name}: fan-out blocked, not ready`, taskStatus(r, F) === 'blocked' && r.result.ready === false && !r.labels.some(l => /^S5\.feature\./.test(l)), r.result && r.result.tasks)
    const d = await runDriver(nw('S5', { items: { [F]: items }, dry_run: true }))
    check(`${F} with ${name}: dry run reports a problem`, problemsOf(d).some(p => p.startsWith(F)), problemsOf(d))
  }
  const over = await runDriver(nw('S5', { items: { [F]: bad[5][1] }, allow_over_budget: true }))
  check(`${F} over max_items with allow_over_budget runs every item`, over.result.ready === true && over.labels.filter(l => /^S5\.feature\./.test(l)).length === max + 1)
} })
await block('Fan-out item validation', async () => { if (S1_FANOUT) {
  const r = await runDriver(nw('S1'), { scoutItems: Array.from({ length: (S1_FANOUT.max_items || 4) + 5 }, (_, i) => ({ id: `a${i}x`, title: `A${i}`, scope: 's' })) })
  check('scout returning more than max_items blocks the fan-out', taskStatus(r, S1_FANOUT.id) === 'blocked' && r.result.ready === false)
  const u = await runDriver(nw('S1'), { onAgent: (p, o) => (isScout(o) ? { items: [{ id: 'orders', title: 'Orders', scope: 's' }], uncovered: ['form: Switchboard'] } : undefined) })
  check('scout with uncovered objects blocks the fan-out', taskStatus(u, S1_FANOUT.id) === 'blocked' && /Switchboard/.test(u.result.tasks.find(t => t.id === S1_FANOUT.id).summary))
  const sc = await runDriver(nw('S1'))
  const scout = sc.calls.find(c => /:scout$/.test(c.label))
  check('scout runs at effort medium with an id pattern in its schema', scout && scout.effort === 'medium' && !!scout.opts.schema.properties.items.items.properties.id.pattern)
  check('scout prompt carries the engagement areas hint', scout && ENG['northwind-demo'].scope.areas_hint.every(h => scout.prompt.includes(h)))
} })

// Prior reuse in a new session, and rerun changing the prompt.
await block('Prior reuse in a new session, and rerun changing the prompt', async () => {
  const first = await runDriver(nw('S2'), { onAgent: (p, o) => (o.label === 'S2.reference' ? { status: 'blocked', summary: 'missing app function', files_changed: [], acceptance: [] } : undefined) })
  const prior = first.result.results
  check('first S2 run: not ready, results map saved for the next session', first.result.ready === false && prior['S2.synth'] && prior['S2.synth'].status === 'done')
  const second = await runDriver(nw('S2', { prior, attempt: 2, since: 'answers.md: Q-orders-002 answered' }))
  check('new session: done tasks are reused without an agent', !second.labels.includes('S2.synth') && second.result.tasks.find(t => t.id === 'S2.synth').reused === true, second.labels.join(' '))
  const ref = second.calls.find(c => c.label === 'S2.reference')
  check('new session: the blocked task runs again with the attempt text', ref && ref.prompt.includes('Attempt 2. Previous result: blocked: missing app function. What changed: answers.md: Q-orders-002 answered'))
  check('new session: downstream of a re-run task runs again', second.labels.includes('S2.golden') && second.result.ready === true, second.result && second.result.blockers)
  const d = await runDriver(nw('S2', { prior, dry_run: true }))
  const typical = d.result.estimate.typical
  check('dry run with prior: the estimate leaves out reused tasks', typical === second.calls.length, `estimate ${typical}, spawned ${second.calls.length}`)
  const all = await runDriver(nw('S2', { prior: second.result.results, attempt: 3 }))
  check('everything done before: only the reviewers and integrate run', all.result.ready === true && all.labels.every(l => /review|integrate/.test(l)), all.labels.join(' '))

  const plain = await runDriver(nw('S2'))
  const rerun = await runDriver(nw('S2', { rerun: ['S2.reference'], attempt: 2, since: 'answers.md: ProductAllocated counts open orders only' }))
  const pr = l => (plain.calls.find(c => c.label === l) || {}).prompt
  const rr = l => (rerun.calls.find(c => c.label === l) || {}).prompt
  check('rerun changes the rerun task\'s prompt (no cached result)', pr('S2.reference') !== rr('S2.reference') && rr('S2.reference').includes('Attempt 2.'))
  check('rerun leaves other prompts unchanged except the attempt line', rr('S2.synth') === pr('S2.synth').replace('Attempt 1.', 'Attempt 2.'))
  const badRerun = await runDriver(nw('S2', { rerun: ['S2.nothing'], dry_run: true }))
  check('rerun naming an unknown task is a problem', problemsOf(badRerun).some(p => /S2\.nothing/.test(p)))
  if (S1_FANOUT) {
    const itemRerun = await runDriver(nw('S1', { rerun: [S1_FANOUT.each.id.replace('{{item.id}}', 'orders')], dry_run: true }))
    check('rerun naming a fan-out item is accepted', problemsOf(itemRerun).length === 0, problemsOf(itemRerun))
  }
})

// Checkpoint on not-ready runs.
await block('Checkpoint on not-ready runs', async () => {
  const r = await runDriver(nw('S4'), { onAgent: (p, o) => (o.label === 'S4.data' ? { status: 'blocked', summary: 'needs a manual step', files_changed: [], acceptance: [], manual_steps: [{ who: 'operator', where: 'windows-box', step: 'confirm Format() output', blocks_gate: true }] } : undefined) })
  const cp = r.calls.find(c => c.label === 'S4:checkpoint')
  check('not ready: one checkpoint agent at effort low', !!cp && cp.effort === 'low' && r.result.checkpoint && r.result.checkpoint.repos.length >= 2)
  check('checkpoint commits WIP with the attempt and writes the runs file and log row', cp && cp.prompt.includes(`WIP S4 northwind-demo: attempt 1 not ready`) && cp.prompt.includes('/runs/S4.json') && cp.prompt.includes('| DATE | S4 | 1 |'))
  check('checkpoint stages done tasks only', cp && cp.prompt.includes('app/') && !cp.prompt.includes(`templates/django-overlay/core/\n- `))
  check('manual steps are collected in the report', r.result.manual_steps.some(m => m.task === 'S4.data' && m.where === 'windows-box'))
  const off = await runDriver(nw('S4', { checkpoint: false }), { onAgent: (p, o) => (o.label === 'S4.data' ? { status: 'blocked', summary: 'x', files_changed: [], acceptance: [] } : undefined) })
  check('checkpoint: false spawns no checkpoint', !off.labels.some(l => /checkpoint/.test(l)) && off.result.ready === false)
  const s0 = await runDriver(nw('S0'), { onAgent: (p, o) => (o.label === 'S0.parse' ? null : undefined) })
  const cp0 = s0.calls.find(c => c.label === 'S0:checkpoint')
  const cpChecks = cp0 ? enumOf(cp0.opts, 'checks') : []
  check('S0 bootstrap checkpoint has the regex fallback (braced) when the guard does not exist yet', cp0 && cpChecks.length === 3 && cpChecks[2].startsWith(bracedHead('s0_checkpoint')) && cpChecks[2].includes("grep -Ev '^toolkit/tests/fixtures/'"), cpChecks)
  check('demo checkpoint runs the staged guard and the history guard before pushing', cpChecks[0] && cpChecks[0].startsWith('mp-data --slot s0_checkpoint guard ') && cpChecks[0].endsWith(' --staged') && cpChecks[1].startsWith('mp-data --slot s0_checkpoint guard ') && cpChecks[1].endsWith(' --history'), cpChecks)
})

// skip_if: real screenshots supplied, so the visuals task drops out and the audit still runs.
await block('skip_if: real screenshots supplied, so the visuals task drops out and ', async () => {
  const a = nw('S6'); a.engagement.assets = { ...(a.engagement.assets || {}), screenshots: '{{p.private}}/assets/screenshots' }
  const d = await runDriver({ ...a, dry_run: true })
  const skipped = (PIPE.stages.find(s => s.id === 'S6').tasks.filter(t => t.skip_if === 'assets.screenshots')).map(t => t.id)
  check('skip_if drops the visuals tasks in the dry run', skipped.length > 0 && skipped.every(id => d.result.tasks_not_applicable.includes(id)), d.result && d.result.tasks_not_applicable)
  const r = await runDriver(a)
  check('skip_if: the audit runs and the stage is ready', r.result.ready === true && r.labels.some(l => /^S6\.audit:r1$/.test(l)) && !r.labels.some(l => skipped.includes(l)), r.labels.join(' '))
})

// lenses_by_kind: the workbook demo's S2 reviewers get the workbook lenses.
await block('lenses_by_kind: the workbook demo\'s S2 reviewers get the workbook lens', async () => {
  const s2rev = PIPE.stages.find(s => s.id === 'S2').tasks.find(t => t.kind === 'review')
  if (s2rev && s2rev.lenses_by_kind && s2rev.lenses_by_kind.xlsm) {
    const ig = allPass['invoicegen-demo S2'], n = allPass['northwind-demo S2']
    // Compare the lens text up to its first placeholder (the driver fills placeholders in lenses).
    const usesLens = (r, raw) => { const p = (r.calls.find(c => c.label === `${s2rev.id}:r1`) || {}).prompt || ''; return p.includes(`Lens: ${raw.split('{{')[0].slice(0, 80)}`) }
    const differ = s2rev.lenses_by_kind.xlsm[0].split('{{')[0] !== s2rev.lenses[0].split('{{')[0]
    check('xlsm engagement: S2 reviewer 1 uses lenses_by_kind.xlsm[0]', differ && usesLens(ig, s2rev.lenses_by_kind.xlsm[0]) && !usesLens(ig, s2rev.lenses[0]))
    check('Access engagement: S2 reviewer 1 uses lenses[0]', usesLens(n, s2rev.lenses[0]))
    check('lens count follows lenses_by_kind', ig.labels.filter(l => l.startsWith(`${s2rev.id}:r`)).length === s2rev.lenses_by_kind.xlsm.length)
  } else check('pipeline.json S2 review has lenses_by_kind.xlsm', false)
})

// Offers, gates and client preconditions.
await block('Offers, gates and client preconditions', async () => {
  const probs = async (mut, st = 'S2') => { const a = clientArgs(st, { dry_run: true }); mut(a); return problemsOf(await runDriver(a)) }
  check('client without host attestation: problem', (await probs(a => { a.host_attested = false })).some(p => /host_attested/.test(p)))
  check('client without consent date: problem', (await probs(a => { a.engagement.data_policy.client_consent.signed_on = null })).some(p => /client_consent/.test(p)))
  check('client with cloud execution: problem', (await probs(a => { a.engagement.data_policy.execution = 'cloud-allowed' })).some(p => /execution/.test(p)))
  check('client S2 without the S1 deposit: problem', (await probs(a => { a.gates.S1.deposit_received = false })).some(p => /deposit/.test(p)))
  check('client S2 without the S1 gate: problem', (await probs(a => { delete a.gates.S1 })).some(p => /gate S1/.test(p)))
  check('client S4 without the S2 gate: problem', (await probs(a => { delete a.gates.S2 }, 'S4')).some(p => /gate S2/.test(p)))
  check('client that bought only the health check cannot run S1', (await probs(a => { a.engagement.commercial.purchased = ['health-check'] }, 'S1')).some(p => /not a stage of the purchased offers/.test(p)))
  check('client that bought only stage1 cannot run S2', (await probs(a => { a.engagement.commercial.purchased = ['stage1'] })).some(p => /not a stage of the purchased offers/.test(p)))
  check('workbook client cannot buy stage1', (await probs(a => { a.engagement.source.kind = 'xlsm'; a.engagement.commercial.purchased = ['stage1'] }, 'S0')).some(p => /stage1 does not apply/.test(p)))
  check('client records inside the pipeline repo: problem', (await probs(a => { a.paths.records = `${PIPELINE_DIR}/engagements/${CLIENT_ID}`; a.engagement.records = a.paths.records })).some(p => /records/.test(p)))
  check('client without paths.records: problem', (await probs(a => { delete a.paths.records; a.engagement.records = null })).some(p => /paths\.records/.test(p)))
  check('design-only .accdt without data_copy at S2: problem', (await probs(a => { a.engagement.source.data_copy = null; a.engagement.source.saved_with_data = false })).some(p => /data_copy/.test(p)))
  check('compiled-only source: problem', (await probs(a => { a.engagement.source.compiled_only = true }, 'S0')).some(p => /compiled_only/.test(p)))
  // walk:client/CW-04: while target.mode is local, only stages of an offer with target_mode local run.
  const LOCAL_MSG = /delivers code to the client's repo: switch target\.mode to github first \(docs\/runbook\.md 1\.6\)/
  const localMode = a => { a.engagement.target = { ...a.engagement.target, mode: 'local', repo: null, local_path: CLIENT_REPO }; a.engagement.commercial.purchased = ['assessment', 'stage2-standard'] }
  const s2Local = await probs(localMode)
  check('client in target.mode local with assessment and stage2-standard: S2 is refused until target.mode is github', s2Local.some(p => p.startsWith('S2 ') && LOCAL_MSG.test(p)), s2Local)
  for (const st of ['S0', 'S1']) {
    const ps = await probs(localMode, st)
    check(`client in target.mode local with assessment and stage2-standard: ${st} (an assessment stage) has no problem`, ps.length === 0, ps)
  }
  // walk:client/CW-13: S3 needs the database.stage1 block.
  const noStage1 = await probs(a => { a.engagement.database.stage1 = null }, 'S3')
  check('client with stage1 purchased and database.stage1 null: S3 problem', noStage1.some(p => p.startsWith('client S3: set database.stage1 (freeze_window, fresh_copy, relink_by, rollback, office_bitness, odbc_driver)')), noStage1)
  check('target.repo given as a URL instead of owner/name: problem', (await probs(a => { a.engagement.target.repo = 'https://github.com/acme-widgets/acme-orders' })).some(p => /target\.repo must be the GitHub owner\/name/.test(p)))
  check('a demo needs no offers, gates or attestation', problemsOf(await runDriver(nw('S2', { dry_run: true }))).length === 0)
  const thrown = await runDriver({ ...clientArgs('S2'), gates: {} })
  check('client real run with missing gates throws before any agent', !!thrown.error && thrown.calls.length === 0 && /gate S1/.test(thrown.error))
})

// Change orders run only the change-order task and the review.
await block('Change orders run only the change-order task and the review', async () => {
  const coTask = PIPE.stages.find(s => s.id === 'S1').tasks.find(t => t.change_order_only)
  if (coTask) {
    const d = await runDriver(clientArgs('S1', { change_order: 'CO-001', dry_run: true }))
    const runs = d.result.tasks.filter(t => t.applies).map(t => t.id)
    check('change order: only the change-order task and the review apply', problemsOf(d).length === 0 && runs.includes(coTask.id) && runs.every(id => id === coTask.id || PIPE.stages[1].tasks.find(t => t.id === id).kind === 'review'), runs.concat(problemsOf(d)))
    const plain = await runDriver(clientArgs('S1', { dry_run: true }))
    check('no change order: the change-order task does not apply', plain.result.tasks_not_applicable.includes(coTask.id))
    const r = await runDriver(clientArgs('S1', { change_order: 'CO-001' }))
    const co = r.calls.find(c => c.label === coTask.id)
    check('change order: {{s.change_order}} is filled and the run is ready', r.result && r.result.ready === true && co && !co.prompt.includes('{{s.change_order}}') && co.prompt.includes('CO-001'), r.error || (r.result && r.result.blockers))
    // walk:client/CW-12: a change-order run reports to runs/S1-CO-NNN.json, so the assessment report stays.
    const runsFile = (run, label) => { const c = run.calls.find(x => x.label === label); return c ? (c.prompt.match(/Write this JSON to (\S+) \(create runs\/ if needed\)/) || [])[1] : undefined }
    const coInteg = r.calls.find(c => c.label === 'S1:integrate')
    check('change order: integrate writes runs/S1-CO-001.json and stages it in the records repo', runsFile(r, 'S1:integrate') === `${CLIENT_RECORDS}/runs/S1-CO-001.json` && coInteg && coInteg.prompt.includes('\n- runs/S1-CO-001.json') && !coInteg.prompt.includes('\n- runs/S1.json'), runsFile(r, 'S1:integrate'))
    const coBlocked = await runDriver(clientArgs('S1', { change_order: 'CO-001' }), { onAgent: (p, o) => (o.label === coTask.id ? { status: 'blocked', summary: 'needs the client', files_changed: [], acceptance: [] } : undefined) })
    check('change order: a not-ready run checkpoints to runs/S1-CO-001.json', runsFile(coBlocked, 'S1:checkpoint') === `${CLIENT_RECORDS}/runs/S1-CO-001.json`, runsFile(coBlocked, 'S1:checkpoint'))
    const assess = await runDriver(clientArgs('S1'))
    check('assessment run (no change order): integrate writes runs/S1.json', assess.result && assess.result.ready === true && runsFile(assess, 'S1:integrate') === `${CLIENT_RECORDS}/runs/S1.json`, assess.error || runsFile(assess, 'S1:integrate'))
    check('bad change-order id: problem', problemsOf(await runDriver(clientArgs('S1', { change_order: 'co1', dry_run: true }))).some(p => /change_order/.test(p)))
    check('change order outside S1: problem', problemsOf(await runDriver(clientArgs('S2', { change_order: 'CO-001', dry_run: true }))).some(p => /change-order task/.test(p)))
  } else check('pipeline.json S1 has a change_order_only task', false)
})

// Validation of the pipeline and the arguments.
await block('Validation of the pipeline and the arguments', async () => {
  const p1 = clone(PIPE); p1.stages.find(s => s.id === 'S0').tasks.find(t => t.id === 'S0.healthcheck').acceptance.push('! uv run toolkit guard --hooks-only')
  check('a command starting with "!" is a problem', problemsOf(await runDriver({ ...nw('S0', { dry_run: true }), pipeline: p1 })).some(p => /starts with "!"/.test(p)))
  const p2 = clone(PIPE); const s0 = p2.stages.find(s => s.id === 'S0'); const guardTask = s0.tasks.find(t => t.id === 'S0.guard'); const hc = s0.tasks.find(t => t.id === 'S0.healthcheck')
  guardTask.owns.push('pipeline:toolkit/tests/fixtures/shared/'); hc.owns.push('pipeline:toolkit/tests/fixtures/shared/')
  check('two tasks that can run at the same time owning one path is a problem', problemsOf(await runDriver({ ...nw('S0', { dry_run: true }), pipeline: p2 })).some(p => /can run at the same time/.test(p)))
  // Pick an S6 build task that can run at the same time as the owner of target:README.md and give it target-repo:README.md.
  const p3 = clone(PIPE); const s6 = p3.stages.find(s => s.id === 'S6')
  const anc = (id, out = new Set()) => { for (const d of (s6.tasks.find(t => t.id === id) || {}).depends_on || []) if (!out.has(d)) { out.add(d); anc(d, out) } return out }
  const readmeOwner = s6.tasks.find(t => (t.owns || []).includes('target:README.md'))
  const other = readmeOwner && s6.tasks.find(t => t.kind === 'build' && t.id !== readmeOwner.id && (!t.applies_to || t.applies_to.includes('accdt')) && !anc(t.id).has(readmeOwner.id) && !anc(readmeOwner.id).has(t.id))
  if (other) {
    other.owns.push('target-repo:README.md')
    const a3 = clientArgs('S6', { dry_run: true }); a3.pipeline = p3
    check(`target:README.md (${readmeOwner.id}) and target-repo:README.md (${other.id}) collide when target.root is "."`, problemsOf(await runDriver(a3)).some(p => /README\.md/.test(p) && /same time/.test(p)))
  }
  const e = clone(ENG['northwind-demo']); delete e.app.slug
  const g = await runDriver({ ...nw('S4', { dry_run: true }), engagement: e })
  check('a missing engagement field is reported as unresolved {{e.app.slug}}', problemsOf(g).some(p => /\{\{e\.app\.slug\}\}/.test(p)))
  const np = await runDriver({ ...nw('S2'), prepared: undefined })
  check('a real run without prepared throws', !!np.error && /prepared/.test(np.error) && np.calls.length === 0)
  const rel = await runDriver({ ...nw('S2', { dry_run: true }), paths: { ...nw('S2').paths, private: '~/engagements/northwind-demo/private' } })
  check('a relative or ~ path is a problem', problemsOf(rel).some(p => /paths\.private/.test(p)))
  const root = await runDriver({ ...nw('S2', { dry_run: true }), paths: { ...nw('S2').paths, target: DEMO_REPO } })
  check('a target path that does not match target.root is a problem', problemsOf(root).some(p => /paths\.target/.test(p)))
  const cap = await runDriver(nw('S2', { max_agents: 3 }))
  check('max_agents caps spawned agents; the rest are blocked; the checkpoint is exempt', cap.result.ready === false && cap.result.agents.spawned === 4 && cap.labels.includes('S2:checkpoint'), cap.labels.join(' '))
  const na = await runDriver(demoArgs('invoicegen-demo', 'S3'))
  const s3 = PIPE.stages.find(s => s.id === 'S3')
  if (s3.applies_to && !s3.applies_to.includes('xlsm')) check('a stage that does not apply to the source kind returns not_applicable, ready, no agents', na.result && na.result.not_applicable === true && na.result.ready === true && na.calls.length === 0)
})

// ---------- command forms (decisions L1, L3.2) ----------
head('Command forms: data-plane calls, mp-run, braced S0 bootstrap')
const NW_ENG = `${PIPELINE_DIR}/engagements/northwind-demo/engagement.json`
const NW_PRIV = `${HOME}/engagements/northwind-demo/private`
const NW_TARGET = `${DEMO_REPO}/${ENG['northwind-demo'].target.root}`
await block('command forms in every all-pass run', async () => {
  const bad = []
  const n = { 'data-plane': 0, 'mp-run': 0, braced: 0 }
  const lineCmd = /^- (mp-data --slot .*|mp-run --eng .*|\. \S+ && export MP_DB_SUFFIX=.*)$/gm
  for (const [key, r] of Object.entries(allPass)) {
    const [id, st] = key.split(' ')
    const engFile = `${PIPELINE_DIR}/engagements/${id}/engagement.json`
    const iSlot = `${st.toLowerCase()}_integrate`
    for (const c of r.calls) {
      const own = labelSlot(c.label)
      const cmds = [...enumCmds(c).map(cmd => [cmd, [own]]), ...[...c.prompt.matchAll(lineCmd)].map(m => [m[1], [own, iSlot]])]
      for (const [cmd, slots] of cmds) {
        const f = formOf(cmd, engFile)
        if (f.form === 'other') { bad.push(`${key} ${c.label}: ${f.why}: ${cmd.slice(0, 160)}`); continue }
        n[f.form]++
        if (own.length <= 24 && !slots.includes(f.slot)) bad.push(`${key} ${c.label}: slot ${f.slot}, want ${slots.join(' or ')}`)
        if ((f.form === 'braced' && st !== 'S0') || (f.form === 'mp-run' && st === 'S0')) bad.push(`${key} ${c.label}: ${f.form} form in ${st} (the braced form is for S0 bootstrap runs, mp-run for every other run)`)
        if (f.form === 'data-plane' && /\$/.test(cmd)) bad.push(`${key} ${c.label}: $ left in a data-plane call: ${cmd}`)
        if (f.inner && /^mp-data\s/.test(f.inner.trim())) bad.push(`${key} ${c.label}: a data-plane call is wrapped: ${cmd.slice(0, 160)}`)
      }
    }
  }
  check(`every command given to an agent is in one of the three forms, with the agent's slot (${n['data-plane']} data-plane, ${n['mp-run']} mp-run, ${n.braced} braced)`, bad.length === 0 && n['data-plane'] > 0 && n['mp-run'] > 0 && n.braced > 0, bad.slice(0, 8).join('\n'))
})
await block('data-plane and mp-run fixtures', async () => {
  const p = clone(PIPE)
  const t = p.stages.find(s => s.id === 'S2').tasks.find(x => x.kind === 'build' && (!x.applies_to || x.applies_to.includes('accdt')))
  t.verify = [...(t.verify || []), "mp-data reference run {{p.records}}/engagement.json --dataset seed-1 --all --summary --out {{p.private}}/review/$MP_DB_SUFFIX/reference.json -k '[orders::'"]
  t.acceptance = [...t.acceptance, `grep -c 'it'"'"'s here' {{p.target}}/README.md`]
  const r = await runDriver({ ...nw('S2'), pipeline: p })
  check('fixture run is ready', r.result && r.result.ready === true, r.error || (r.result && r.result.blockers))
  const want = slot => `mp-data --slot ${slot} reference run ${NW_ENG} --dataset seed-1 --all --summary --out ${NW_PRIV}/review/${slot}/reference.json -k '[orders::'`
  const b = r.calls.find(c => c.label === t.id)
  const rv = r.calls.find(c => c.label === 'S2.review:r1')
  check('(a) a data-plane verify line reaches its builder unwrapped, with --slot and the literal slot', b && b.prompt.includes(`- ${want(labelSlot(t.id))}\n`), b && b.prompt.split('\n').filter(l => l.includes('reference run')).join('\n'))
  check('(a) and each reviewer with the reviewer\'s slot', rv && rv.prompt.includes(`- ${want('s2_review_r1')}\n`))
  check('(a) no $MP_DB_SUFFIX is left in any data-plane call', r.calls.every(c => !/(^|\n)- mp-data [^\n]*\$MP_DB_SUFFIX/.test(c.prompt)))
  const acc = b ? enumOf(b.opts, 'acceptance') : []
  const q = acc.find(c => c.includes('README.md'))
  const f = q ? formOf(q, NW_ENG) : {}
  check('mp-run: the anchored line splits into exactly mp-run --eng <records>/engagement.json --slot <slot> -- <command>, and <command> is the filled command', f.form === 'mp-run' && f.slot === labelSlot(t.id) && f.inner === `grep -c 'it'"'"'s here' ${NW_TARGET}/README.md`, q)
  check("mp-run: each ' inside the command is written '\\''", q && q.endsWith(`-- 'grep -c '\\''it'\\''"'\\''"'\\''s here'\\'' ${NW_TARGET}/README.md'`), q)
  const s1 = await runDriver(nw('S1', { dry_run: true }))
  const fan = S1_FANOUT && s1.result.items_from[S1_FANOUT.id]
  const ff = fan ? formOf(fan.cmd, NW_ENG) : {}
  check('items_from.cmd is anchored too (mp-run, prepare slot) and runs exactly jq -c \'<jq>\' <file>', !S1_FANOUT || (ff.form === 'mp-run' && ff.slot === 's1_prepare' && ff.inner === `jq -c '${fan.jq}' ${fan.file}`), fan && fan.cmd)
  const c0 = await runDriver(clientArgs('S0'))
  const ws = c0.calls.find(c => c.label === 'S0.workspace')
  const forms = ws ? enumOf(ws.opts, 'acceptance').map(c => formOf(c, `${CLIENT_RECORDS}/engagement.json`).form) : []
  check('client S0 (delivery mode): no braced form; every command is mp-run or a data-plane call', forms.length > 0 && forms.every(x => x === 'mp-run' || x === 'data-plane'), forms)
})
await block('(b) compound mp-data lines are problems', async () => {
  const G = 'mp-data guard {{p.records}}/engagement.json --repo {{p.pipeline}} --repo {{p.target_repo}}'
  const F = 'mp-data fetch {{p.records}}/engagement.json --verify'
  const MSG = 'mp-data must be a standalone data-plane call'
  const exitProblems = async cmd => { const p = clone(PIPE); p.stages.find(s => s.id === 'S2').exit_criteria.push(cmd); return problemsOf(await runDriver({ ...nw('S2', { dry_run: true }), pipeline: p })) }
  for (const cmd of [`${G} && echo ok`, `if test -f toolkit/commands/guard.py; then ${G}; fi`, `x=$(${F})`, `test -n "$(${F})"`, `MP_ONLY_AREAS=orders ${F}`, `${F} | tee /tmp/fetch.json`, `${F} > /tmp/fetch.json`, `${F} 2>&1`, `cd {{p.target}} && ${F}`, `${F} --out "$HOME/x"`, `{ ${F}; }`, `true || ${F}`, `${F}; echo done`, `${F} &`]) {
    const ps = await exitProblems(cmd)
    check(`(b) problem for: ${cmd}`, ps.some(x => x.startsWith(`S2.exit_criteria: ${MSG}`)), ps)
  }
  for (const cmd of ['command -v mp-data', "grep -q 'mp-data guard' README.md", 'cmp -s scripts/mp-data $HOME/.local/bin/mp-data', `${G} -k '[orders::'`, `mp-data --if-built ${G.slice('mp-data '.length)}`, `mp-data equivalence {{p.records}}/engagement.json --junit-dir {{p.private}}/results/S2/$MP_DB_SUFFIX`, 'mp-data equivalence {{p.records}}/engagement.json --junit-dir "{{p.private}}/results/S2/$MP_DB_SUFFIX"']) {
    const ps = await exitProblems(cmd)
    check(`(b) no problem for: ${cmd}`, ps.length === 0, ps)
  }
  const dq = clone(PIPE); dq.stages.find(s => s.id === 'S2').exit_criteria.push('mp-data equivalence {{p.records}}/engagement.json --junit-dir "{{p.private}}/results/S2/$MP_DB_SUFFIX"')
  const dqx = ((await runDriver({ ...nw('S2', { dry_run: true }), pipeline: dq })).result.exit_criteria || []).slice(-1)[0]
  check('(a) $MP_DB_SUFFIX inside a double-quoted argument becomes the literal slot', dqx === `mp-data --slot s2_integrate equivalence ${NW_ENG} --junit-dir "${NW_PRIV}/results/S2/s2_integrate"`, dqx)
  check('(b) a data-plane call that passes --slot itself is a problem', (await exitProblems(`mp-data --slot s2 ${G.slice('mp-data '.length)}`)).some(x => /must not pass --slot/.test(x)))
  const p = clone(PIPE); const t = p.stages.find(s => s.id === 'S2').tasks.find(x => x.kind === 'build'); t.acceptance.push(`${F} && echo ok`)
  check('(b) a compound line in a task acceptance names the task', problemsOf(await runDriver({ ...nw('S2', { dry_run: true }), pipeline: p })).some(x => x.startsWith(`${t.id}.acceptance: ${MSG}`)))
  const nl = clone(PIPE); const tn = nl.stages.find(s => s.id === 'S2').tasks.find(x => x.kind === 'build'); tn.acceptance.push("uv run python -c 'import sys\nprint(1)'")
  check('a command that spans two lines is a problem (the client Bash hook blocks newlines)', problemsOf(await runDriver({ ...nw('S2', { dry_run: true }), pipeline: nl })).some(x => x.startsWith(`${tn.id}.acceptance: command spans more than one line`)))
  // Environment commands are never anchored and run outside Claude for clients: they may span lines (L12.5),
  // but like every pipeline.json command they call mp-data only as a standalone data-plane call (contracts §3).
  const pe = clone(PIPE); pe.environment.push({ id: 'probe_env', stages: ['S2'], check: 'test -x $HOME/.local/bin/mp-data &&\n  test -s {{p.records}}/engagement.json' })
  const de = await runDriver({ ...nw('S2', { dry_run: true }), pipeline: pe })
  const pc = ((de.result && de.result.environment) || []).find(x => x.id === 'probe_env')
  check('environment commands are exempt from the one-line rule: a two-line check is no problem and comes back filled, as written', problemsOf(de).length === 0 && !!pc && pc.check === `test -x $HOME/.local/bin/mp-data &&\n  test -s ${NW_ENG}`, problemsOf(de).concat(pc ? [pc.check] : []))
  const pm = clone(PIPE); pm.environment.push({ id: 'probe_env', stages: ['S2'], check: `test -x $HOME/.local/bin/mp-data && ${F} >/dev/null` })
  check('(b) an environment check with a compound mp-data call is a problem too', problemsOf(await runDriver({ ...nw('S2', { dry_run: true }), pipeline: pm })).some(x => x.startsWith(`environment.probe_env: ${MSG}`)))
  // items_from commands are run by the orchestrator as one Bash call (blocked by the client hook if multi-line).
  if (S1_FANOUT && S1_FANOUT.items_from) {
    const pj = clone(PIPE); pj.stages.find(s => s.id === 'S1').tasks.find(x => x.id === S1_FANOUT.id).items_from.jq += '\n| .'
    check('an items_from jq program that spans two lines is a problem', problemsOf(await runDriver({ ...nw('S1', { dry_run: true }), pipeline: pj })).some(x => x.startsWith(`${S1_FANOUT.id}.items_from: command spans more than one line`)))
    const pf = clone(PIPE); delete pf.stages.find(s => s.id === 'S1').tasks.find(x => x.id === S1_FANOUT.id).items_from.file
    check('an items_from without a file is a problem', problemsOf(await runDriver({ ...nw('S1', { dry_run: true }), pipeline: pf })).some(x => x.startsWith(`${S1_FANOUT.id}.items_from must have a file and a jq program`)))
  }
  if (S5_FANOUT) {
    const p5 = clone(PIPE); const f5 = p5.stages.find(s => s.id === 'S5').tasks.find(x => x.id === S5_FANOUT.id); f5.each.verify = [...(f5.each.verify || []), `ds=$(jq -r .x {{p.records}}/engagement.json) && ${F}`]
    check('(b) a compound line in a fan-out each template names the fan-out', problemsOf(await runDriver({ ...nw('S5', { dry_run: true, items: { [S5_FANOUT.id]: itemsFor('northwind-demo') } }), pipeline: p5 })).some(x => x.startsWith(`${S5_FANOUT.id}.each.verify: ${MSG}`)))
  }
})
// Decisions L13.3 and L13.7 on the merged pipeline.json: seed databases are built by `uv run toolkit reference
// build --dataset seeds|seed-N` and the engagement reference DB by `mp-data reference build --dataset engagement`
// (`--dataset all` exits 2), seeds first where a list has both; S5 and S6 prepare start with
// `cd {{p.target}}/app && uv sync --locked`, which comes back in the mp-run form without a cd to the pipeline.
await block('reference build forms and the app sync in prepare (L13.3, L13.7)', async () => {
  const lists = []
  for (const s of PIPE.stages) {
    lists.push([`${s.id}.exit_criteria`, s.exit_criteria || []], [`${s.id}.prepare`, s.prepare || []])
    for (const t of s.tasks || []) { const src = t.each || t; for (const k of ['acceptance', 'verify']) lists.push([`${t.id}.${k}`, src[k] || []]) }
  }
  const bad = []
  let n = 0
  for (const [where, cmds] of lists) {
    let engAt = -1
    cmds.forEach((c, i) => {
      if (typeof c !== 'string' || !/\breference build\b/.test(c)) return
      n++
      const ds = (c.match(/--dataset[= ](\S+)/) || [])[1] || '(none)'
      if (c.trim().startsWith('mp-data ')) { if (ds !== 'engagement') bad.push(`${where}: mp-data reference build with --dataset ${ds} (only engagement): ${c}`); else if (engAt < 0) engAt = i }
      else if (!/^(seeds|seed-[\w{}.]+)$/.test(ds)) bad.push(`${where}: a reference build outside mp-data with --dataset ${ds} (only seeds or seed-N): ${c}`)
      else if (engAt >= 0) bad.push(`${where}: the seeds build comes after the engagement build: ${c}`)
    })
  }
  check(`(L13.3) every reference build line uses its form (${n} lines): mp-data for engagement, uv run toolkit for seeds, seeds first`, n > 0 && bad.length === 0, bad.slice(0, 5).join('\n'))
  const SYNC = 'cd {{p.target}}/app && uv sync --locked'
  for (const st of ['S5', 'S6']) {
    const raw = (PIPE.stages.find(s => s.id === st) || {}).prepare || []
    check(`(L13.7) ${st} prepare starts with ${SYNC}`, raw[0] === SYNC, raw[0])
    for (const [who, a, eng, target] of [['northwind-demo', nw(st, { dry_run: true }), NW_ENG, NW_TARGET], ['client', clientArgs(st, { dry_run: true }), `${CLIENT_RECORDS}/engagement.json`, CLIENT_REPO]]) {
      const d = await runDriver(a)
      const first = ((d.result && d.result.prepare) || [])[0] || ''
      const f = formOf(first, eng)
      check(`(L13.7) ${who} ${st}: the app sync comes back as mp-run with slot ${st.toLowerCase()}_prepare, starting with its own cd`, f.form === 'mp-run' && f.slot === `${st.toLowerCase()}_prepare` && f.inner === `cd ${target}/app && uv sync --locked`, first)
    }
  }
})

// ---------- environment list (decision L4) ----------
head('Environment list derived from environment[].stages, kinds and when')
const usesMssql = e => [(e.database || {}).primary, ...((e.database || {}).verify_also || [])].includes('mssql')
await block('(c) environment list per engagement and stage', async () => {
  check('(c) no stage in pipeline.json has preflight', PIPE.stages.every(s => !Object.prototype.hasOwnProperty.call(s, 'preflight')), PIPE.stages.filter(s => 'preflight' in s).map(s => s.id))
  const mssqlItem = (PIPE.environment || []).find(x => x.id === 'mssql')
  check('(c) the mssql item applies only behind a when', !!(mssqlItem && mssqlItem.when))
  const envIds = (PIPE.environment || []).map(x => x.id)
  check('(c) jq comes right after uv in the environment order', envIds.includes('uv') && envIds.indexOf('jq') === envIds.indexOf('uv') + 1, envIds)
  const callsJq = x => [x.check, x.fix, x.when].some(c => typeof c === 'string' && /(^|[\s;&|(])jq\s/.test(c))
  const early = (PIPE.environment || []).filter((x, i) => x.id !== 'jq' && callsJq(x) && i < envIds.indexOf('jq')).map(x => x.id)
  check('(c) every environment item that calls jq comes after the jq item', envIds.includes('jq') && early.length === 0, early)
  for (const id of ['northwind-demo', 'invoicegen-demo', 'client']) {
    for (const st of STAGES) {
      const a = id === 'client' ? clientArgs(st, { dry_run: true }) : demoArgs(id, st, { dry_run: true })
      const d = await runDriver(a)
      if (!d.result || d.result.not_applicable) continue
      const e = a.engagement
      const want = (PIPE.environment || []).filter(x => (x.stages || []).includes(st) && (!x.kinds || x.kinds.includes(e.source.kind))).map(x => x.id)
      const got = d.result.preflight || []
      check(`(c) ${id} ${st}: preflight is the environment items for the stage and source kind, in environment order`, JSON.stringify(got) === JSON.stringify(want), `got  ${got.join(' ')}\nwant ${want.join(' ')}`)
      check(`(c) ${id} ${st}: jq, jdk and bwrap are in the list`, ['jq', 'jdk', 'bwrap'].every(x => got.includes(x)), got)
      const m = (d.result.environment || []).find(x => x.id === 'mssql')
      const records = (a.paths.records || `${PIPELINE_DIR}/engagements/${e.id}`).replace(/\/+$/, '')
      const mStages = mssqlItem ? mssqlItem.stages : []
      check(`(c) ${id} ${st}: mssql ${mStages.includes(st) ? 'is listed with its when on this engagement file' : 'is not listed'}`, mStages.includes(st) ? !!(m && m.when && m.when.includes(`${records}/engagement.json`)) : !m, m)
      const wrapped = (d.result.environment || []).flatMap(x => [x.check, x.fix, x.when].filter(Boolean)).filter(c => /^(mp-run |mp-data --slot |\. \S+ && export MP_DB_SUFFIX=)/.test(c) || /\{\{\s*[a-z]+\.[\w.]+\s*\}\}/.test(c))
      check(`(c) ${id} ${st}: environment commands are filled and never anchored`, wrapped.length === 0, wrapped[0])
    }
  }
  // The orchestrator runs when before the item; for mssql it must pass exactly when the engagement uses SQL Server.
  const hasTool = t => { const r = spawnSync('bash', ['-c', `command -v ${t}`], { stdio: 'ignore' }); return r.status === 0 }
  if (mssqlItem && mssqlItem.when && hasTool('jq')) {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mp-when-'))
    try {
      fs.mkdirSync(path.join(tmp, 'home/.config/modernization'), { recursive: true })
      fs.writeFileSync(path.join(tmp, 'home/.config/modernization/env'), '')
      fs.mkdirSync(path.join(tmp, 'records'))
      const when = (await runDriver(demoArgs('northwind-demo', 'S4', { dry_run: true }))).result.environment.find(x => x.id === 'mssql').when.split(NW_ENG).join(`${tmp}/records/engagement.json`)
      const pg = clone(CLIENT); pg.database = { ...pg.database, primary: 'postgresql', verify_also: [] }
      const pgPlus = clone(pg); pgPlus.database.verify_also = ['mssql']
      for (const [name, e] of [['northwind-demo', ENG['northwind-demo']], ['invoicegen-demo', ENG['invoicegen-demo']], ['client (mssql primary)', CLIENT], ['client (postgresql only)', pg], ['client (postgresql, verify_also mssql)', pgPlus]]) {
        fs.writeFileSync(path.join(tmp, 'records/engagement.json'), JSON.stringify(e))
        const st = spawnSync('bash', ['-c', when], { env: { ...process.env, HOME: path.join(tmp, 'home') }, stdio: 'ignore' }).status
        check(`(c) mssql when ${usesMssql(e) ? 'passes' : 'fails'} for ${name}`, usesMssql(e) ? st === 0 : st !== 0, `exit ${st}`)
      }
    } finally { fs.rmSync(tmp, { recursive: true, force: true }) }
  } else console.log('  note: jq (or bash) not found, so the mssql when was not executed')
  const p = clone(PIPE); p.stages.find(s => s.id === 'S2').preflight = ['uv']
  check('(c) a stage preflight key is a problem', problemsOf(await runDriver({ ...nw('S4', { dry_run: true }), pipeline: p })).some(x => x === 'S2: remove stage preflight; environment[].stages is the source'))
  const p2 = clone(PIPE); p2.environment.push({ id: 'extra', stages: ['S9'], check: 'true' })
  check('(c) an environment item with an unknown stage is a problem', problemsOf(await runDriver({ ...nw('S2', { dry_run: true }), pipeline: p2 })).some(x => /environment\.extra: unknown stage/.test(x)))
})

// ---------- stage offers (decision L6) ----------
head('Stage offers')
await block('(d) stage offers keys', async () => {
  const keys = Object.keys(PIPE.offers || {})
  check('(d) every stage lists offers, keys of pipeline.json offers', PIPE.stages.every(s => Array.isArray(s.offers) && s.offers.length > 0 && s.offers.every(k => keys.includes(k))), PIPE.stages.map(s => `${s.id}: ${JSON.stringify(s.offers)}`).join('\n'))
  const p = clone(PIPE); p.stages.find(s => s.id === 'S3').offers = ['stage1', 'stage3']
  check('(d) an unknown offers key is a problem', problemsOf(await runDriver({ ...nw('S2', { dry_run: true }), pipeline: p })).some(x => x.startsWith('S3.offers: unknown offer "stage3"')))
  const p2 = clone(PIPE); p2.stages.find(s => s.id === 'S1').offer = 'assessment'
  check('(d) the old offer text field is a problem', problemsOf(await runDriver({ ...nw('S1', { dry_run: true }), pipeline: p2 })).some(x => x.startsWith('S1: stage field offer is replaced by offers')))
  const p3 = clone(PIPE); p3.stages.find(s => s.id === 'S0').offers = 'health-check'
  check('(d) offers that is not a list is a problem', problemsOf(await runDriver({ ...nw('S0', { dry_run: true }), pipeline: p3 })).some(x => x.startsWith('S0.offers must be a list')))
})

// ---------- client sessions, pushes, demo history check, reviewers (decisions L3.3, L3.5, L3.6, L10) ----------
head('Client sessions, pushes, history checks and reviewer zones')
await block('client and demo prompts', async () => {
  const HOOK = 'This session blocks any Bash call that is not one mp-run or mp-data call.'
  const ADHOC = `Run ad-hoc commands as \`mp-run --eng ${CLIENT_RECORDS}/engagement.json --slot <your slot> -- '<command>'\`.`
  const cl = await runDriver(clientArgs('S2'))
  check('client: every agent prompt carries the Bash-hook rule and the mp-run form', cl.calls.length > 0 && cl.calls.every(c => c.prompt.includes(HOOK) && c.prompt.includes(ADHOC)))
  const demo = allPass['northwind-demo S2']
  check('demo: no prompt carries the client Bash-hook rule', demo && demo.calls.every(c => !c.prompt.includes(HOOK)))
  check('demo S1+: prompts explain the mp-run form', demo && demo.calls.every(c => c.prompt.includes(`mp-run --eng ${NW_ENG} --slot <your slot> -- '<command>'`)))
  const stage = PIPE.stages.find(s => s.id === 'S2')
  const ci = cl.calls.find(c => c.label === 'S2:integrate')
  const tBranch = fillBranch(PIPE.branches.target, CLIENT, stage)
  check('client integrate pushes the target repo with mp-data push, never git push', ci && ci.prompt.includes(`mp-data --slot s2_integrate push ${CLIENT_RECORDS}/engagement.json --branch ${tBranch}`) && !ci.prompt.includes(`git -C ${CLIENT_REPO} push`))
  check('client integrate runs git through mp-run', ci && ci.prompt.includes(`run each git and date command in these steps as \`mp-run --eng ${CLIENT_RECORDS}/engagement.json --slot s2_integrate -- '<command>'\``))
  const gitHow = (eng, slot) => `run each git and date command in these steps as \`mp-run --eng ${eng} --slot ${slot} -- '<command>'\``
  const dI = demo && demo.calls.find(c => c.label === 'S2:integrate')
  check('demo integrate from S1 on also runs git through mp-run, without the client hook text', dI && dI.prompt.includes(`As in a client session, ${gitHow(NW_ENG, 's2_integrate')}`))
  const s0i = allPass['northwind-demo S0'] && allPass['northwind-demo S0'].calls.find(c => c.label === 'S0:integrate')
  check('demo S0 bootstrap integrate runs git directly (mp-run does not exist yet)', s0i && !s0i.prompt.includes('run each git and date command'))
  check('client integrate checks have no history scan',ci && !enumOf(ci.opts, 'exit_criteria').some(c => /--history/.test(c)))
  const di = demo && demo.calls.find(c => c.label === 'S2:integrate')
  const dChecks = di ? enumOf(di.opts, 'exit_criteria') : []
  check('demo integrate pushes the target repo with git push', di && di.prompt.includes(`git -C ${DEMO_REPO} push origin ${fillBranch(PIPE.branches.target, ENG['northwind-demo'], stage)}`))
  const pushU = Object.values(allPass).flatMap(r => r.calls).concat(cl.calls).filter(c => /:(integrate|checkpoint)$/.test(c.label) && /\bpush -u\b/.test(c.prompt)).map(c => c.label)
  check('no integrate or checkpoint prompt pushes with -u (mp-run keeps .git/config read-only)', pushU.length === 0, pushU.slice(0, 5))
  // L12.5: demos run the history guard at integrate AND checkpoint; clients never.
  const blockSynth = (p, o) => (o.label === 'S2.synth' ? { status: 'blocked', summary: 'needs an answer', files_changed: [], acceptance: [] } : undefined)
  const dcp = (await runDriver(nw('S2'), { onAgent: blockSynth })).calls.find(c => c.label === 'S2:checkpoint')
  const dcpChecks = dcp ? enumOf(dcp.opts, 'checks') : []
  const dGuard = flag => `mp-data --slot s2_checkpoint guard ${NW_ENG} --repo ${PIPELINE_DIR} --repo ${DEMO_REPO} ${flag}`
  check('demo S2 checkpoint runs the staged guard and the history guard as data-plane calls', JSON.stringify(dcpChecks) === JSON.stringify([dGuard('--staged'), dGuard('--history')]), dcpChecks)
  check('demo S2 checkpoint runs git through mp-run with its own slot', dcp && dcp.prompt.includes(`As in a client session, ${gitHow(NW_ENG, 's2_checkpoint')}`))
  const ccp = (await runDriver(clientArgs('S2'), { onAgent: blockSynth })).calls.find(c => c.label === 'S2:checkpoint')
  const ccpChecks = ccp ? enumOf(ccp.opts, 'checks') : []
  check('client checkpoint runs the staged guard only (no history scan)', JSON.stringify(ccpChecks) === JSON.stringify([`mp-data --slot s2_checkpoint guard ${CLIENT_RECORDS}/engagement.json --repo ${PIPELINE_DIR} --repo ${CLIENT_REPO} --staged`]), ccpChecks)
  check('demo integrate checks always include the history guard as a data-plane call', dChecks.includes(`mp-data --slot s2_integrate guard ${NW_ENG} --repo ${PIPELINE_DIR} --repo ${DEMO_REPO} --history`), dChecks.slice(0, 5))
  check('demo integrate says its first checks cover the staged files and the history', di && di.prompt.includes('(the first ones check the staged files and the history of both repos; the rest are the stage exit criteria)'))
  check('client integrate says its first checks cover the staged files only', ci && ci.prompt.includes('(the first ones check the staged files; the rest are the stage exit criteria)'))
  check('every prompt says only mp-data reads the data zone', [...cl.calls, ...(demo ? demo.calls : [])].every(c => c.prompt.includes('(data zone: only mp-data reads it)')))
  check('no integrate or checkpoint check runs gh repo view (no compound visibility line)',Object.values(allPass).every(r => r.calls.every(c => !enumCmds(c).some(x => x.includes('gh repo view')))))
  const rv = demo && demo.calls.find(c => c.label === 'S2.review:r1')
  check('reviewers: review/<slot>/ is the design zone and may be read; data-zone paths never', rv && rv.prompt.includes(`${NW_PRIV}/review/s2_review_r1/`) && /review\/s2_review_r1\/\. That directory is in the design zone: you may read/.test(rv.prompt) && rv.prompt.includes('Never read a data-zone path') && !/exit code only/.test(rv.prompt))
})

// ---------- agent estimate table ----------
head('Agent estimates (typical/worst; S1 with the scout, S5 with items from areas_hint)')
const pad = (s, n) => String(s).padEnd(n)
console.log(`  ${pad('stage', 7)}${pad('budget', 8)}${pad('northwind-demo', 16)}${pad('invoicegen-demo', 17)}client`)
const tot = { 'northwind-demo': [0, 0], 'invoicegen-demo': [0, 0], client: [0, 0] }
for (const row of table) {
  console.log(`  ${pad(row.stage, 7)}${pad(row.budget == null ? '-' : row.budget, 8)}${pad(row['northwind-demo'], 16)}${pad(row['invoicegen-demo'], 17)}${row.client}`)
  for (const k of Object.keys(tot)) { const m = /^(\d+)\/(\d+)$/.exec(row[k]); if (m) { tot[k][0] += +m[1]; tot[k][1] += +m[2] } }
}
console.log(`  ${pad('total', 15)}${pad(tot['northwind-demo'].join('/'), 16)}${pad(tot['invoicegen-demo'].join('/'), 17)}${tot.client.join('/')}`)

report()
function report() {
  console.log(`\n${passed} checks passed, ${failed.length} failed`)
  if (failed.length) { console.log(`Failed:\n- ${failed.join('\n- ')}`); process.exit(1) }
  process.exit(0)
}
