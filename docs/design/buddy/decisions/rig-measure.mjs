#!/usr/bin/env node
// The rig spike's measurement (#3887): each candidate extension of the agent orb, in a real
// Electron renderer, against the bars in docs/design/buddy/rig.md. The record is rig.md beside
// this file; the raw numbers of every run are in rig-runs/.
//
// It needs a gateway on a scratch home serving this checkout's web/dist, built with the harness:
//
//     VITE_BUDDY_RIG=1 npm run build --workspace web
//     PERSONALCLAW_HOME=<scratch home> .venv/bin/personalclaw gateway --no-open --no-crons --port <port>
//     node docs/design/buddy/decisions/rig-measure.mjs --home <scratch home>
//     node docs/design/buddy/decisions/rig-measure.mjs --summary
//
// The gateway is the one the named home's record names (scripts/lib/named_home.mjs); no home, or
// the default one, is refused before anything starts. Each run opens one scratch window
// (rig-window.cjs) for one candidate and writes rig-runs/<os>-<candidate>-<n>.json, the orb at the
// buddy's 48 px (--size 24 measures another size, and names its files <os>-<candidate>-24px-<n>;
// --fps 30 caps the frames of the candidate that owns its frame loop, and adds 30fps- to the name;
// --out DIR writes them elsewhere: a trial run's numbers are not the record's).
//
// A CPU number is only as good as the machine is quiet, so a run starts only while the 5-minute
// load average is under --max-load (30), re-checking each minute for up to --wait (60) minutes in
// all. Past that the runs go anyway, and every sample carries the load it was taken under.
//
// Bundle size is read from the build output: the chunks the window loaded for the candidate that
// it had not loaded for the harness alone, gzipped here at level 9.

import { execFileSync, spawn } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { gzipSync } from 'node:zlib'
import { homeArg, scratchGatewayOrExit } from '../../../../scripts/lib/named_home.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO = path.resolve(HERE, '..', '..', '..', '..')
const DIST = path.join(REPO, 'web', 'dist')
const RECORD = path.join(HERE, 'rig-runs')
const CANDIDATES = ['in-place', 'layered']

/** The bars of docs/design/buddy/rig.md, "What is measured". */
export const BARS = { restCpu: 0.5, loopCpu: 5, stillFrames: 0, memoryMb: 60, bundleKb: 150, retintFrames: 1 }

/** n, mean, median, 95th percentile and maximum of `values`. */
export function stats(values) {
  const v = values.filter((x) => typeof x === 'number' && Number.isFinite(x)).sort((a, b) => a - b)
  if (!v.length) return { n: 0, mean: null, p50: null, p95: null, max: null }
  const at = (q) => v[Math.min(v.length - 1, Math.floor(q * v.length))]
  return { n: v.length, mean: v.reduce((a, b) => a + b, 0) / v.length, p50: at(0.5), p95: at(0.95), max: v[v.length - 1] }
}

/** The assets `index.html` loads before any route: the shared app code. */
export function entryAssets(html) {
  const out = new Set()
  for (const m of html.matchAll(/<(?:script|link)\b[^>]*?(?:src|href)="(\/assets\/[^"]+)"/g)) out.add(m[1])
  return out
}

/** The chunks only the candidate loads (loaded with it, not by the harness alone), and every chunk
 *  the route loads beyond the shared app code. */
export function chunksOf({ baseline, loaded, entry }) {
  const seen = new Set(baseline)
  const unique = (list) => [...new Set(list)].sort()
  return {
    candidate: unique(loaded.filter((a) => !seen.has(a))),
    route: unique(loaded.filter((a) => !entry.has(a))),
  }
}

const mean = (rows, pick) => stats(rows.map(pick)).mean

/** Each measure of one run against its bar. `bundle` is the candidate's chunks with their sizes. */
export function verdicts(run) {
  const restCpu = mean(run.rest.rows, (r) => r.renderer?.cpu)
  const loopCpu = mean(run.loop.rows, (r) => r.renderer?.cpu)
  const highest = (rows) => Math.max(0, ...rows.map((r) => (r.renderer?.workingSetKb ?? 0) / 1024))
  const memoryMb = highest([...run.rest.rows, ...run.loop.rows, ...run.reduced.rows])
  const harnessMb = run.harness ? highest(run.harness.rows) : null
  const bundleKb = run.bundle.candidate.reduce((a, c) => a + c.gzip, 0) / 1024
  const follow = (r) => r?.framesToFollow?.[run.candidate] ?? null
  const retint = [follow(run.retint.moving), follow(run.retint.still)]
  const dial = (b, e, key) => run.dials.find((d) => d.bounciness === b && d.expressiveness === e && d.key === key)
  const springy = ['spread', 'lift', 'swirl']
  const overshootScales = springy.every((k) => dial(1, 1, k)?.overshoot > 0.02 && dial(0, 1, k)?.overshoot < 0.01)
  const amplitudeScales = [...springy, 'sink'].every((k) => dial(1, 1, k)?.amplitude > dial(1, 0, k)?.amplitude * 1.5)
  const most = Math.max(0, ...Object.values(run.reduced.valueChanges ?? {}))
  return [
    { measure: 'CPU at rest, visible', value: restCpu, unit: '% of one core', bar: `under ${BARS.restCpu}`, passes: restCpu !== null && restCpu < BARS.restCpu },
    { measure: 'CPU while animating', value: loopCpu, unit: '% of one core', bar: `under ${BARS.loopCpu}`, passes: loopCpu !== null && loopCpu < BARS.loopCpu },
    { measure: 'Frames while still', value: run.rest.callbacks, unit: 'frame callbacks in 60 s', bar: 'zero', passes: run.rest.callbacks === BARS.stillFrames },
    { measure: 'Memory', value: { renderer: memoryMb, harnessAlone: harnessMb }, unit: 'MB, the renderer’s working set at its highest, and with the harness alone', bar: `under ${BARS.memoryMb}`, passes: memoryMb < BARS.memoryMb },
    { measure: 'Bundle size', value: bundleKb, unit: 'KB gzip, the chunks only the candidate loads', bar: `under ${BARS.bundleKb}`, passes: bundleKb < BARS.bundleKb },
    { measure: 'Live retint', value: retint, unit: 'frames to follow: moving, still', bar: 'within one frame', passes: retint.every((f) => f !== null && f <= BARS.retintFrames) },
    { measure: 'The dials', value: { overshootScales, amplitudeScales }, unit: 'overshoot by bounciness, travel by expressiveness', bar: 'both scale', passes: overshootScales && amplitudeScales },
    { measure: 'Reduced motion', value: { frames: run.reduced.callbacks, mostChanges: most, steps: run.reduced.steps }, unit: 'frame callbacks; most changes of one pose value', bar: 'no value passes between poses', passes: run.reduced.setting === true && most <= run.reduced.steps },
    { measure: 'The working hold, alone', value: run.hold ? { cpu: mean(run.hold.rows, (r) => r.renderer?.cpu), fps: run.hold.frames / run.hold.rows.length } : null, unit: '% of one core; frames a second', bar: 'none: read beside the loop', passes: null },
  ]
}

/** The provisional choice by the issue's rule: the smallest by the candidate's own chunks among
 *  the candidates whose every run passes the at-rest and the animating CPU bars. */
export function provisional(runs) {
  const byCandidate = new Map()
  for (const run of runs) byCandidate.set(run.candidate, [...(byCandidate.get(run.candidate) ?? []), run])
  const pick = (m, name) => m.find((x) => x.measure === name)
  const rows = [...byCandidate].map(([candidate, list]) => {
    const v = list.map(verdicts)
    const cpu = v.every((m) => pick(m, 'CPU at rest, visible').passes && pick(m, 'CPU while animating').passes)
    const kb = Math.max(...v.map((m) => pick(m, 'Bundle size').value))
    return { candidate, runs: list.length, passesCpu: cpu, bundleKb: kb }
  })
  const passing = rows.filter((r) => r.passesCpu).sort((a, b) => a.bundleKb - b.bundleKb)
  const tie = passing.length > 1 && passing[0].bundleKb === passing[1].bundleKb
  return { rows, choice: passing.length && !tie ? passing[0].candidate : null }
}

function parseArgs(argv) {
  const out = { candidates: [], runs: 1, seconds: 60, reducedSeconds: 20, harnessSeconds: 10, holdSeconds: 20, stepMs: 800, settleMs: 5000, size: 48, maxLoad: 30, wait: 60, summary: false, out: RECORD, fps: null }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    const next = () => argv[++i]
    if (a === '--home') next()
    else if (a.startsWith('--home=')) continue
    else if (a === '--candidate') out.candidates.push(next())
    else if (a === '--runs') out.runs = Number(next())
    else if (a === '--seconds') out.seconds = Number(next())
    else if (a === '--reduced-seconds') out.reducedSeconds = Number(next())
    else if (a === '--step-ms') out.stepMs = Number(next())
    else if (a === '--size') out.size = Number(next())
    else if (a === '--fps') out.fps = Number(next())
    else if (a === '--max-load') out.maxLoad = Number(next())
    else if (a === '--wait') out.wait = Number(next())
    else if (a === '--summary') out.summary = true
    else if (a === '--out') out.out = path.resolve(next())
    else { process.stderr.write(`unknown flag: ${a}\n`); process.exit(2) }
  }
  if (!out.candidates.length) out.candidates = [...CANDIDATES]
  for (const c of out.candidates) if (!CANDIDATES.includes(c)) { process.stderr.write(`no candidate ${c}: ${CANDIDATES.join(', ')}\n`); process.exit(2) }
  return out
}

const osName = () => ({ darwin: 'macos', linux: 'linux' })[process.platform] ?? process.platform
const sh = (cmd, args) => { try { return execFileSync(cmd, args, { encoding: 'utf8' }).trim() } catch { return null } }

function environment() {
  return {
    os: osName(),
    osVersion: process.platform === 'darwin' ? `macOS ${sh('sw_vers', ['-productVersion'])} (${sh('sw_vers', ['-buildVersion'])})` : `${os.type()} ${os.release()}`,
    arch: process.arch,
    machine: process.platform === 'darwin' ? sh('sysctl', ['-n', 'hw.model']) : null,
    cpu: os.cpus()[0]?.model ?? null,
    cores: os.cpus().length,
    memoryGb: Math.round(os.totalmem() / 2 ** 30),
    commit: sh('git', ['-C', REPO, 'rev-parse', '--short=12', 'HEAD']),
    webChanged: (sh('git', ['-C', REPO, 'status', '--porcelain', '--', 'web/src']) ?? '') !== '',
  }
}

/** Waits for the 5-minute load to fall under `maxLoad`, until `end` at the latest: one hour for a
 *  whole invocation, not one for each run. Says whether it ran out. */
async function quiet(maxLoad, end) {
  for (;;) {
    const load = os.loadavg()[1]
    if (load < maxLoad) return { waited: false, load }
    if (Date.now() >= end) return { waited: true, load }
    process.stderr.write(`rig-measure: 5-minute load ${load.toFixed(1)} is not under ${maxLoad}; checking again in a minute\n`)
    await new Promise((resolve) => setTimeout(resolve, 60_000))
  }
}

function runWindow(config) {
  const electron = createRequire(path.join(REPO, 'desktop', 'package.json'))('electron')
  return new Promise((resolve, reject) => {
    const child = spawn(electron, [path.join(HERE, 'rig-window.cjs')], { stdio: ['pipe', 'pipe', 'inherit'] })
    let out = ''
    child.stdout.on('data', (d) => { out += d })
    child.on('error', reject)
    child.on('exit', (code) => {
      const line = out.split('\n').find((l) => l.startsWith('RESULT '))
      if (code === 0 && line) resolve(JSON.parse(line.slice('RESULT '.length)))
      else reject(new Error(`the scratch window ended with status ${code} and no result`))
    })
    child.stdin.end(`${JSON.stringify(config)}\n`)
  })
}

function sized(names) {
  return names.map((name) => {
    const file = path.join(DIST, name)
    if (!existsSync(file)) throw new Error(`${name} is not in web/dist: the gateway serves another build`)
    const bytes = readFileSync(file)
    return { name, bytes: bytes.length, gzip: gzipSync(bytes, { level: 9 }).length }
  })
}

function readRuns(dir) {
  if (!existsSync(dir)) return []
  return readdirSync(dir).filter((f) => f.endsWith('.json')).sort().map((f) => ({ file: f, ...JSON.parse(readFileSync(path.join(dir, f), 'utf8')) }))
}

function fmt(v) {
  if (v === null || v === undefined) return 'n/a'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(2)
  if (Array.isArray(v)) return v.map(fmt).join(', ')
  return Object.entries(v).map(([k, x]) => `${k} ${fmt(x)}`).join('; ')
}

const verdictWord = (m) => (m.passes === null ? 'info  ' : m.passes ? 'passes' : 'FAILS ')

function summary(dir) {
  const runs = readRuns(dir)
  for (const run of runs) {
    const load = stats([...run.rest.rows, ...run.loop.rows].map((r) => r.load[1]))
    console.log(`\n${run.file}: ${run.env.osVersion}, ${run.env.cpu}, Electron ${run.versions.electron}, build ${run.env.commit}, 5-minute load ${fmt(load.mean)} (max ${fmt(load.max)})`)
    for (const m of verdicts(run)) console.log(`  ${verdictWord(m)}  ${m.measure}: ${fmt(m.value)} (${m.unit}; bar ${m.bar})`)
  }
  const setting = (r) => `${r.size ?? 48} px${r.fps ? `, ${r.fps} fps` : ''}`
  for (const at of [...new Set(runs.map(setting))]) {
    const choice = provisional(runs.filter((r) => setting(r) === at))
    console.log(`\nprovisional at ${at}: ${choice.choice ?? 'none'}`)
    for (const r of choice.rows) console.log(`  ${r.candidate}: ${r.runs} runs, CPU bars ${r.passesCpu ? 'pass' : 'fail'}, ${fmt(r.bundleKb)} KB`)
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2))
  if (args.summary) { summary(args.out); return }
  const gateway = scratchGatewayOrExit(homeArg())
  const index = path.join(DIST, 'index.html')
  const assets = existsSync(path.join(DIST, 'assets')) ? readdirSync(path.join(DIST, 'assets')) : []
  if (!existsSync(index) || !assets.some((a) => a.startsWith('BuddyRigHarness-'))) {
    console.error('web/dist carries no rig harness: build it with VITE_BUDDY_RIG=1 npm run build --workspace web')
    process.exit(1)
  }
  const entry = entryAssets(readFileSync(index, 'utf8'))
  mkdirSync(args.out, { recursive: true })
  const env = environment()
  const end = Date.now() + args.wait * 60_000
  for (let n = 0; n < args.runs; n++) {
    for (const candidate of args.candidates) {
      const gate = await quiet(args.maxLoad, end)
      const loadAtStart = os.loadavg()
      const raw = await runWindow({ url: gateway.url, token: gateway.token, candidate, seconds: args.seconds, reducedSeconds: args.reducedSeconds, harnessSeconds: args.harnessSeconds, holdSeconds: args.holdSeconds, stepMs: args.stepMs, settleMs: args.settleMs, size: args.size, fps: args.fps })
      const chunks = chunksOf({ baseline: raw.resources.baseline.map((p) => p.replace(/^\//, '')), loaded: raw.resources.loaded.map((p) => p.replace(/^\//, '')), entry: new Set([...entry].map((p) => p.replace(/^\//, ''))) })
      const run = { ...raw, size: args.size, env, gate: { maxLoad: args.maxLoad, ...gate, loadAtStart, loadAtEnd: os.loadavg() }, bundle: { candidate: sized(chunks.candidate), route: sized(chunks.route) } }
      const stem = `${env.os}-${candidate}-${args.size === 48 ? '' : `${args.size}px-`}${args.fps ? `${args.fps}fps-` : ''}`
      const taken = readdirSync(args.out).filter((f) => f.startsWith(stem) && /^\d+\.json$/.test(f.slice(stem.length))).length
      const file = path.join(args.out, `${stem}${taken + 1}.json`)
      writeFileSync(file, `${JSON.stringify(run, null, 1)}\n`)
      console.log(`${path.relative(REPO, file)}:`)
      for (const m of verdicts(run)) console.log(`  ${verdictWord(m)}  ${m.measure}: ${fmt(m.value)} (${m.unit}; bar ${m.bar})`)
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((err) => { console.error(err && err.stack ? err.stack : String(err)); process.exit(1) })
}
