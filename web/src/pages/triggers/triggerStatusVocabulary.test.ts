/**
 * A trigger's status vocabulary is TOTAL, and DERIVED from the backend rather than mirrored by hand.
 *
 * ── THE DEFECT (issue 496) ───────────────────────────────────────────────────────────────────────
 * One renderer was fed several vocabularies. Measured against a live gateway (`GET /api/triggers`,
 * one seeded trigger per state the backend can report), before:
 *
 *   schedule  system:notification-digest  last_status=degraded  → degraded  WARN     ✅
 *   schedule  Lap2 regression probe       last_status=ok  never run → **ok  OK-GREEN**  ❌ never ran
 *   event     app-note-mirror             health=parked state=parked → **never run GREY** ❌
 *   store     Docs mirror                 health=degraded  → degraded WARN, reason WITHHELD  ❌
 *
 * Both directions of the same mistake. The dot UNDERSTATED for the event kind (a parked trigger
 * rendered as the neutral "no data" dot, indistinguishable from one that had never fired) and
 * OVERSTATED for the schedule kind (6 of 11 rows drew an ok-green tick beside the word "never",
 * because `Trigger.health_status` DEFAULTS to `ok` and a healthy default was being read as an
 * observed run).
 *
 * ── WHY THIS RAIL IS DERIVED AND NOT A LIST ──────────────────────────────────────────────────────
 * The renderers were already covered by tests carrying HAND-WRITTEN vocabularies
 * (`const HEALTH = ['ok', 'degraded', 'parked', 'failing']`). A hand-written mirror proves nothing
 * about the backend unless something proves it is still a mirror — and the four hook statuses that
 * fell to "never run" grey (`queued`, `blocked`, `advisory`, `held_for_rung`) were missed by exactly
 * that gap. So every vocabulary here is READ OUT OF THE PYTHON SOURCE at test time. A new
 * `TriggerHealth` member, a new `TriggerState`, a new `Outcome`, a new hook status, or a re-mapped
 * hook status all fail HERE instead of turning grey in front of a user.
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { pyDictKeys, pyDictToEnumValues, pyEnumMembers, pyMethod } from '../../design/pySource'
import { statusMeta, triggerHealthMeta, triggerStatusMeta, explainsCause } from '../schedule/scheduleMeta'

const SRC = join(process.cwd(), '..', 'src', 'personalclaw')
const py = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const MODELS = py('triggers/models.py')
const HISTORY = py('triggers/history.py')
const HOOKS = py('hooks.py')

/** The neutral fall-through every one of these vocabularies must avoid. */
const NEVER = statusMeta(null)
const isDefault = (m: { label: string; tone: string }) =>
  m.label === NEVER.label && m.tone === NEVER.tone

/** The hook `last_status` vocabulary, read from the single choke point that closes it. */
const hookStatuses = (): string[] => {
  const record = pyMethod(HOOKS, '    def _record(status: str) -> None:')
  const tuple = record.split('hook.last_status = (')[1]?.split(')')[0] ?? ''
  return [...tuple.matchAll(/"([a-z_]+)"/g)].map((m) => m[1])
}

describe('the backend vocabularies this UI renders are non-empty', () => {
  // Guards every assertion below: a renamed class would otherwise make the sweeps vacuous.
  it('reads all four out of the Python source', () => {
    expect(pyEnumMembers(MODELS, 'TriggerHealth').length).toBeGreaterThanOrEqual(4)
    expect(pyEnumMembers(MODELS, 'TriggerState').length).toBeGreaterThanOrEqual(6)
    expect(pyEnumMembers(MODELS, 'Outcome').length).toBeGreaterThanOrEqual(12)
    expect(hookStatuses().length).toBeGreaterThanOrEqual(9)
    expect(Object.keys(pyDictKeys(HISTORY, 'HOOK_STATUS_TO_OUTCOME')).length).toBeGreaterThanOrEqual(11)
  })
})

describe('every TriggerHealth member renders as something', () => {
  it('none falls through to the neutral never-run dot', () => {
    const fell = pyEnumMembers(MODELS, 'TriggerHealth').filter((h) =>
      isDefault(triggerHealthMeta(h, 'active')),
    )
    expect(fell, 'a health rollup with no tone renders as "no data"').toEqual([])
  })

  it('keeps every non-ok member visually DISTINCT from ok and from each other', () => {
    const members = pyEnumMembers(MODELS, 'TriggerHealth')
    const tones = new Set(members.map((h) => triggerHealthMeta(h, 'active').tone))
    expect(tones.size, 'two health states sharing a tone is the defect').toBe(members.length)
  })
})

describe('every TriggerState member renders as something', () => {
  it('none falls through to the neutral never-run dot', () => {
    const fell = pyEnumMembers(MODELS, 'TriggerState')
      .filter((s) => s !== 'active')
      .filter((s) => isDefault(triggerHealthMeta('ok', s)))
    expect(fell, 'a lifecycle state with no tone cannot be seen').toEqual([])
  })

  it('a healthy rollup never hides a stopped automation', () => {
    // The dangerous direction: `health: ok` on a state that will not fire must not read as running.
    for (const s of pyEnumMembers(MODELS, 'TriggerState')) {
      if (s === 'active') continue
      expect(triggerHealthMeta('ok', s).label, `state=${s} must speak over an ok rollup`).not.toBe('ok')
    }
  })
})

describe('every Outcome member renders as something', () => {
  it('none falls through to the neutral never-run dot', () => {
    const fell = pyEnumMembers(MODELS, 'Outcome').filter((o) => isDefault(statusMeta(o)))
    expect(fell, 'a typed fire outcome with no rendering reads as "never ran"').toEqual([])
  })
})

describe('every hook last_status renders as something, toned like the outcome it maps to', () => {
  it('none falls through to the neutral never-run dot', () => {
    // Measured before: `queued`, `blocked`, `advisory` and `held_for_rung` ALL returned
    // "never run" in neutral grey — and `blocked` is a REGRESSION, the deleted `statusDot` toned
    // it danger, so consolidating two local mappers into one silently dropped it.
    const fell = hookStatuses().filter((s) => isDefault(statusMeta(s)))
    expect(fell, 'a hook status with no rendering reads as "never ran"').toEqual([])
  })

  it('agrees with HOOK_STATUS_TO_OUTCOME rather than holding a second opinion', () => {
    // 🔑 THE DERIVATION. `triggers/history.py` already decides what each hook status MEANS. The dot
    // reads that decision instead of choosing again, so it cannot disagree with the runs feed about
    // the same fire — and a re-mapped status reds here.
    const mapped = pyDictToEnumValues(HISTORY, 'HOOK_STATUS_TO_OUTCOME', MODELS, 'Outcome')
    expect(Object.keys(mapped).length).toBeGreaterThanOrEqual(11)
    for (const [status, outcome] of Object.entries(mapped)) {
      // TONE, not glyph. The tone IS the claim — how alarmed the user should be — and that is what
      // must not disagree with the runs feed. The glyph is allowed to be MORE specific than the
      // outcome it maps to: `timeout` maps to `FAILED` and draws a Clock rather than an X, because
      // "it ran out of time" is a truer picture of the same danger. What is never allowed is the
      // neutral circle, which is the no-data glyph and would erase the status entirely.
      expect(statusMeta(status).tone, `${status} → ${outcome}: tone must match the outcome`).toBe(
        statusMeta(outcome).tone,
      )
      expect(statusMeta(status).icon, `${status}: must not draw the no-data glyph`).not.toBe(
        NEVER.icon,
      )
    }
  })

  it('every status the hook can WRITE is one the outcome table can map', () => {
    // Two lists, one vocabulary: a status written but unmapped would fall to history's own
    // `RAN if last_run` default and report a held action as a successful one.
    const mapped = new Set(pyDictKeys(HISTORY, 'HOOK_STATUS_TO_OUTCOME'))
    expect(hookStatuses().filter((s) => !mapped.has(s))).toEqual([])
  })
})

describe('the prefix match is bounded to whole tokens', () => {
  // The precedent: a naive substring match once captured a word's own negation. `statusMeta` matches
  // the suppression family by the `skipped_` PREFIX rather than enumerating it, so the prefix has to
  // be proved to capture exactly the members that mean "nothing happened" — and no other vocabulary's
  // member may be swallowed by it.
  it('captures only the inert suppression family, never another vocabulary', () => {
    const inert = pyEnumMembers(MODELS, 'Outcome').filter((o) => o.startsWith('skipped_'))
    expect(inert.length, 'the family exists').toBeGreaterThanOrEqual(6)
    for (const o of inert) {
      expect(statusMeta(o).tone, `${o} is inert — neutral, not an alarm`).toBe(
        'var(--color-on-surface-low)',
      )
    }
    // Nothing OUTSIDE that family may start with the prefix and be swept up by it.
    const swept = [
      ...pyEnumMembers(MODELS, 'TriggerHealth'),
      ...pyEnumMembers(MODELS, 'TriggerState'),
      ...hookStatuses(),
    ].filter((t) => t.startsWith('skipped_') && !inert.includes(t))
    // `skipped_incident` is a hook status deliberately in the family (history.py maps it to
    // SKIPPED_GATE), so it is allowed to be swept — anything else would be an accident.
    expect(swept.filter((t) => t !== 'skipped_incident')).toEqual([])
  })

  it('no member is rendered as a member it merely CONTAINS', () => {
    // The exact-match branches are order-dependent, so a token that contains another (`blocked`
    // inside `blocked_injection`, `paused` inside `autopaused`, `ran` inside `ran_late`) must not be
    // matched by the shorter one's branch. Checked THROUGH THE MAPPER THAT OWNS EACH VOCABULARY:
    // `statusMeta` speaks run outcomes and hook statuses, `triggerHealthMeta` speaks health + state.
    // Applying the wrong one is how this test first went red — and is a smaller version of the same
    // mistake the whole issue is about.
    const byMapper: Array<[string, string[], (t: string) => { label: string }]> = [
      ['run outcomes + hook statuses', [...pyEnumMembers(MODELS, 'Outcome'), ...hookStatuses()], (t) => statusMeta(t)],
      ['health rollups', pyEnumMembers(MODELS, 'TriggerHealth'), (t) => triggerHealthMeta(t, 'active')],
      ['lifecycle states', pyEnumMembers(MODELS, 'TriggerState'), (t) => triggerHealthMeta('ok', t)],
    ]
    let checked = 0
    for (const [name, tokens, render] of byMapper) {
      const uniq = [...new Set(tokens)]
      for (const short of uniq) {
        for (const long of uniq) {
          if (short === long || !long.includes(short)) continue
          checked += 1
          expect(
            render(long).label,
            `${name}: ${long} must not render as ${short} — a substring must not win`,
          ).not.toBe(render(short).label)
        }
      }
    }
    // Vacuity floor: if the vocabularies stop containing each other this proves nothing, and the
    // pairs that exist today (paused/autopaused, ran/ran_late, blocked/blocked_injection) are
    // exactly why the rule is here.
    expect(checked, 'the containment pairs this codebase actually has').toBeGreaterThanOrEqual(3)
  })

  it('no two statuses share a LABEL at different alarm levels', () => {
    // 🔴 The generalisation of the above, and it caught a real one: aliasing the hook status
    // `blocked` onto `Outcome.REFUSED` while keeping the hook's own word gave `blocked` (warn) and
    // `blocked_injection` (danger) the identical label "blocked". One word, two alarm levels, no way
    // to tell a routine hook refusal from a screened attack — this issue's defect in miniature.
    const byLabel = new Map<string, Set<string>>()
    for (const t of [...pyEnumMembers(MODELS, 'Outcome'), ...hookStatuses()]) {
      const m = statusMeta(t)
      if (!byLabel.has(m.label)) byLabel.set(m.label, new Set())
      byLabel.get(m.label)!.add(m.tone)
    }
    const clashes = [...byLabel].filter(([, tones]) => tones.size > 1).map(([label]) => label)
    expect(clashes, 'one label must mean one alarm level').toEqual([])
  })
})

describe('triggerStatusMeta: never-run is not a healthy run', () => {
  it('a DEFAULT ok rollup on a trigger that never fired reads "never run", not ok', () => {
    // The measured overstatement: 6 of 11 live schedule rows, five of them the SYSTEM triggers a
    // fresh home registers. `health_status` defaults to `ok` on the dataclass, so on a never-fired
    // row it is the absence of an observation, not one.
    const m = triggerStatusMeta({ runStatus: null, health: 'ok', state: 'active', hasRun: false })
    expect(m.label).toBe('never run')
    expect(m.tone).toBe('var(--color-on-surface-low)')
  })

  it('the same rollup DOES speak once something has run', () => {
    const m = triggerStatusMeta({ runStatus: null, health: 'ok', state: 'active', hasRun: true })
    expect(m.label).toBe('ok')
    expect(m.tone).toBe('var(--color-ok)')
  })

  it('a non-ok rollup speaks even with nothing run — it is an observation either way', () => {
    // Symmetry, and the safe direction: `degraded` is written BY something happening.
    for (const h of pyEnumMembers(MODELS, 'TriggerHealth')) {
      if (h === 'ok') continue
      const m = triggerStatusMeta({ runStatus: null, health: h, hasRun: false })
      expect(m.label, `health=${h} must not read as never-run`).not.toBe('never run')
      expect(m.tone, `health=${h} must not read as ok`).not.toBe('var(--color-ok)')
    }
  })

  it('a stopped lifecycle state outranks the run row AND the rollup', () => {
    for (const s of pyEnumMembers(MODELS, 'TriggerState')) {
      if (s === 'active') continue
      const m = triggerStatusMeta({ runStatus: 'ran', health: 'ok', state: s, hasRun: true })
      expect(m.label, `state=${s} must not be hidden by a successful run`).not.toBe('ran')
    }
  })

  it('the reaper disagreement still resolves to the health rollup (#685)', () => {
    // The regression guard for the case that owned this reconciler before: a killed turn writes
    // health=degraded while the run row it launched still says `success`.
    const m = triggerStatusMeta({ runStatus: 'success', health: 'degraded', hasRun: true })
    expect(m.label).toBe('degraded')
    expect(m.tone).toBe('var(--color-warning)')
  })

  it('the legacy `error` health keeps its danger shape', () => {
    // Pre-`TriggerHealth` rows carry `error` on the same field; `triggerHealthMeta` has no branch
    // for it, so the fall-through to the run-outcome mapper is load-bearing.
    const m = triggerStatusMeta({ runStatus: 'success', health: 'error', hasRun: true })
    expect(m.label).toBe('error')
    expect(m.tone).toBe('var(--color-danger)')
  })

  it('an ok rollup defers to the run row, so the outcome vocabulary is untouched', () => {
    expect(triggerStatusMeta({ runStatus: 'launched', health: 'ok', hasRun: true }).label).toBe('launched')
    expect(triggerStatusMeta({ runStatus: 'ran_late', health: 'ok', hasRun: true }).label).toBe('ran late')
    expect(triggerStatusMeta({ runStatus: 'skipped_gate', health: 'ok', hasRun: true }).label).toBe('gate')
    expect(triggerStatusMeta({}).label).toBe('never run')
  })
})

describe('explainsCause gates the reason on the reason still being true', () => {
  it('withholds it from a healthy row', () => {
    // `last_error_summary` is written on a failing exit and NEVER cleared on recovery — `unpark_due`
    // resets `health_status` to ok and leaves the text — so an ungated reason reports a fixed fault.
    expect(explainsCause(triggerStatusMeta({ runStatus: 'ran', hasRun: true }))).toBe(false)
    expect(explainsCause(triggerStatusMeta({ runStatus: null, health: 'ok', hasRun: true }))).toBe(false)
  })

  it('withholds it from a row that has not run yet', () => {
    expect(explainsCause(triggerStatusMeta({ health: 'ok', hasRun: false }))).toBe(false)
  })

  it('shows it for every non-ok health member and every stopped state', () => {
    for (const h of pyEnumMembers(MODELS, 'TriggerHealth')) {
      if (h === 'ok') continue
      expect(explainsCause(triggerStatusMeta({ health: h })), `health=${h}`).toBe(true)
    }
    for (const s of pyEnumMembers(MODELS, 'TriggerState')) {
      if (s === 'active') continue
      expect(explainsCause(triggerStatusMeta({ health: 'ok', state: s })), `state=${s}`).toBe(true)
    }
  })
})
