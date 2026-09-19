// Report shaping for the app-bundle UI validation harness (scripts/app_ui_validate.mjs).
//
// Kept apart from the Playwright driver on purpose: the verdict rules are the part
// of the harness that can quietly lie (a leg that never ran reading as green), so
// they live in a pure module with unit tests (app_validate_report.test.mjs) rather
// than being tangled up with browser plumbing that only a live gateway can exercise.

/** The standard legs, in drive order. `id` is the report key and the screenshot
 *  filename stem; `title` is what a human reads. */
export const LEGS = [
  { id: 'store-source', title: 'Register the bundle as a local Store source' },
  { id: 'store-card', title: 'Store card renders name, type, description, permissions, source kind' },
  { id: 'ui-install', title: 'Install through the UI, consent dialog included' },
  { id: 'library-and-tools', title: 'Lands in the Library and its tools render on the Tools page' },
  { id: 'tool-invoke', title: 'Invoke a tool from the UI and capture the result' },
  { id: 'reactivate', title: 'Deactivate then reactivate round-trip' },
  // LAST, not (as issue #2588 first sketched) straight after `tool-invoke`. It is the only
  // destructive leg: it removes the app and puts it back, and its second arm force-removes
  // it. Running it before `reactivate` would leave that leg asserting a toggle on an app
  // this one had just uninstalled, and a mid-leg abort would report the absence as a
  // reactivate defect. Everything it needs — a tool to write through and data on disk —
  // exists from `tool-invoke` onward, so last satisfies the ordering requirement too.
  { id: 'uninstall-preserves-data', title: "Uninstall keeps the app's data; force uninstall does not" },
]

/** The data states core reports as SEPARATE facts, plus the two non-answers.
 *
 *  `describe_app_data()` returns `present` and `entries` as distinct fields on purpose:
 *  "this app keeps no data" and "it has a data folder and it happens to be empty" are
 *  different promises, and collapsing absent into declared-false is a bug this project has
 *  now hit nine times. A leg that renders the two the same re-introduces it one level up,
 *  so the classifier NAMES which state it saw and the driver reports that name verbatim.
 *
 *  `BLOCKED` and `UNKNOWN` are non-answers, not passes: the first is core refusing a
 *  keep-data uninstall while an earlier unconsumed copy of the data is still on disk
 *  (#2585), the second is the gateway telling us nothing at all. */
export const APP_DATA = Object.freeze({
  ABSENT: 'absent',
  EMPTY: 'empty',
  PRESENT: 'present',
  BLOCKED: 'blocked',
  UNKNOWN: 'unknown',
})

/** Classify `GET /api/apps/{name}/uninstall-preview`'s `data` block.
 *
 *  Returns `{ state, present, entries, unconsumed, reason }`. `reason` is empty for
 *  `PRESENT` (the only state in which a preservation claim is observable) and a ready-to-use
 *  skip reason for every other state — the reason text lives here rather than in the driver
 *  because "a leg that could not run says why" is the harness's core honesty property, and
 *  this module is the part of it that has tests. */
export function classifyAppData(facts) {
  const unknown = {
    state: APP_DATA.UNKNOWN,
    present: false,
    entries: 0,
    unconsumed: [],
    reason: 'the gateway reported no data facts for this app, so the leg cannot state which '
      + 'of the three data states it saw — and an unnamed state is not an answer',
  }
  if (!facts || typeof facts !== 'object' || typeof facts.present !== 'boolean') return unknown
  const entries = Number.isInteger(facts.entries) ? facts.entries : 0
  const unconsumed = Array.isArray(facts.unconsumed) ? facts.unconsumed.map(String) : []
  const base = { present: facts.present, entries, unconsumed }
  if (unconsumed.length) {
    return {
      ...base,
      state: APP_DATA.BLOCKED,
      reason: `an earlier unconsumed copy of this app's data is still on disk (${unconsumed.join(', ')}), `
        + 'so core refuses a keep-data uninstall by design (#2585) — the refusal is correct '
        + 'behaviour, but it means preservation cannot be observed on this run',
    }
  }
  if (!facts.present) {
    return {
      ...base,
      state: APP_DATA.ABSENT,
      reason: 'the app declares no data directory (data=absent), so a removal has nothing to '
        + 'preserve — this is the absence of an answer, not a passing preservation claim',
    }
  }
  if (entries === 0) {
    return {
      ...base,
      state: APP_DATA.EMPTY,
      reason: 'the app declares a data directory and it is EMPTY (data=present, 0 entries), '
        + 'which is a different fact from declaring none — but a preservation check that '
        + 'preserves nothing cannot tell a working preserve from a broken wipe',
    }
  }
  return { ...base, state: APP_DATA.PRESENT, reason: '' }
}

export const STATUS = Object.freeze({
  PASS: 'PASS',
  FAIL: 'FAIL',
  SKIPPED: 'SKIPPED',
  /** Internal-only: a leg the driver never reached. Never survives into a report —
   *  `finalizeLegs` converts it to SKIPPED with a reason. */
  PENDING: 'PENDING',
})

/** The reason stamped on a leg the driver never reached. A leg that did not run
 *  must not read as PASS and must not read as an unexplained SKIPPED either. */
export const NOT_REACHED_REASON =
  'leg did not run — an earlier leg in this bundle did not complete'

/** A fresh, un-run leg. */
export function newLeg({ id, title }) {
  if (!id) throw new Error('newLeg: id is required')
  return { id, title: title ?? id, status: STATUS.PENDING, reason: '', screenshots: [], notes: [], details: {} }
}

/** All legs for one bundle, in drive order, all PENDING. */
export function newLegs(legs = LEGS) {
  return legs.map(newLeg)
}

function findLeg(legs, id) {
  const leg = legs.find((l) => l.id === id)
  if (!leg) throw new Error(`unknown leg id: ${id}`)
  return leg
}

function applyEvidence(leg, { screenshots = [], notes = [], details = {} } = {}) {
  leg.screenshots = [...leg.screenshots, ...screenshots.filter(Boolean)]
  leg.notes = [...leg.notes, ...notes.filter(Boolean)]
  leg.details = { ...leg.details, ...details }
}

/** Mark a leg PASS. Evidence is additive so a driver can attach screenshots as it
 *  goes and still settle the status at the end. */
export function passLeg(legs, id, evidence) {
  const leg = findLeg(legs, id)
  leg.status = STATUS.PASS
  leg.reason = ''
  applyEvidence(leg, evidence)
  return leg
}

/** Mark a leg FAIL. A failure without a reason is useless to whoever reads the
 *  report, so the reason is mandatory here too. */
export function failLeg(legs, id, reason, evidence) {
  if (!reason || !String(reason).trim()) throw new Error(`failLeg(${id}): a reason is required`)
  const leg = findLeg(legs, id)
  leg.status = STATUS.FAIL
  leg.reason = String(reason).trim()
  applyEvidence(leg, evidence)
  return leg
}

/** Mark a leg SKIPPED. The reason string is mandatory — the whole point of SKIPPED
 *  is that it says why it could not run, so an empty reason is a programming error,
 *  not a silently-tolerated blank. */
export function skipLeg(legs, id, reason, evidence) {
  if (!reason || !String(reason).trim()) throw new Error(`skipLeg(${id}): a reason is required`)
  const leg = findLeg(legs, id)
  leg.status = STATUS.SKIPPED
  leg.reason = String(reason).trim()
  applyEvidence(leg, evidence)
  return leg
}

/** Attach evidence without settling status (screenshots taken mid-leg). */
export function noteLeg(legs, id, evidence) {
  applyEvidence(findLeg(legs, id), evidence)
}

/** Convert every leg the driver never reached into SKIPPED with an explicit reason.
 *  `reason` lets the caller name the actual cause (a crashed gateway, an install that
 *  never landed) instead of the generic fallback. */
export function finalizeLegs(legs, reason = NOT_REACHED_REASON) {
  const why = String(reason || '').trim() || NOT_REACHED_REASON
  for (const leg of legs) {
    if (leg.status === STATUS.PENDING) {
      leg.status = STATUS.SKIPPED
      leg.reason = why
    }
  }
  return legs
}

/** PASS only when every leg passed; FAIL when any leg failed; PARTIAL otherwise
 *  (i.e. no failures but at least one leg could not run). An empty leg list is
 *  FAIL — "nothing ran" is not a pass. */
export function bundleVerdict(legs) {
  if (!legs.length) return STATUS.FAIL
  if (legs.some((l) => l.status === STATUS.FAIL)) return STATUS.FAIL
  if (legs.every((l) => l.status === STATUS.PASS)) return STATUS.PASS
  return 'PARTIAL'
}

/** One bundle's report block. Legs are finalized first, so a report can never
 *  carry a PENDING leg. */
export function shapeBundleReport({ bundle, path, app = null, legs, consoleErrors = [], pageErrors = [], notReachedReason }) {
  const finalized = finalizeLegs(legs, notReachedReason)
  return {
    bundle,
    path,
    app,
    verdict: bundleVerdict(finalized),
    legs: finalized.map((l) => ({ ...l })),
    consoleErrors: [...consoleErrors],
    pageErrors: [...pageErrors],
  }
}

/** Per-status leg counts across every bundle. */
export function countStatuses(bundles) {
  const totals = { PASS: 0, FAIL: 0, SKIPPED: 0 }
  for (const b of bundles) for (const l of b.legs) totals[l.status] = (totals[l.status] ?? 0) + 1
  return totals
}

/** A run fails iff some leg failed. A SKIPPED leg is an honest non-result, not a
 *  failure — it is the caller's job to decide whether the skip is acceptable. */
export function exitCodeFor(bundles) {
  return bundles.some((b) => b.verdict === STATUS.FAIL) ? 1 : 0
}

export const REPORT_SCHEMA = 'personalclaw.app-ui-validation/1'

/** The whole machine-readable report. */
export function shapeReport({ bundles, harness = {}, model = null, generatedAt }) {
  return {
    schema: REPORT_SCHEMA,
    generatedAt: generatedAt ?? new Date().toISOString(),
    harness,
    model,
    legs: LEGS.map((l) => ({ ...l })),
    bundles,
    totals: countStatuses(bundles),
    exitCode: exitCodeFor(bundles),
  }
}

/** Human-readable digest of a report — the same facts as the JSON, one line per leg. */
export function formatReport(report) {
  const out = []
  for (const b of report.bundles) {
    out.push(`${b.bundle}: ${b.verdict}${b.app?.version ? ` (v${b.app.version})` : ''}`)
    for (const l of b.legs) {
      const why = l.reason ? ` — ${l.reason}` : ''
      out.push(`  ${l.status.padEnd(7)} ${l.id}${why}`)
      for (const s of l.screenshots) out.push(`          shot: ${s}`)
    }
  }
  const t = report.totals
  out.push(`totals: ${t.PASS} PASS · ${t.FAIL} FAIL · ${t.SKIPPED} SKIPPED`)
  return out.join('\n')
}
