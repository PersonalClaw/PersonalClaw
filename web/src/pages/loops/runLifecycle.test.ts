import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { AWAITING_EMITTER, RUN_LIFECYCLE, unwrapRunBatch } from './useRunStream'

// EventSource silently DROPS event types with no registered listener, and `useRunStream` builds
// its listeners by iterating THIS const. So a member missing from the union is not a bug you can
// see — it is a live update that never arrives. This test pins the membership the plan-review
// surface depends on, so a refactor of the array can't silently drop one.
describe('RUN_LIFECYCLE union membership', () => {
  it('carries the UNIVERSAL-PLANNING plan-review events (WF2UNI-10)', () => {
    for (const ev of ['plan_streaming', 'revision', 'confirmation', 'demotion'] as const) {
      expect(RUN_LIFECYCLE).toContain(ev)
    }
  })

  it('has no duplicate members (a dup double-registers a listener)', () => {
    expect(new Set(RUN_LIFECYCLE).size).toBe(RUN_LIFECYCLE.length)
  })
})

// ── Every member is BACKED by real Python, or named as awaiting one (issue 607) ──
//
// `cycle_score` sat in this union — and in the source-attribution comment above it, listed beside
// `phase_advance` as if the goal/design kinds emitted it — while never appearing anywhere in `src/`
// in the repository's entire history. No publish site, no ledger kind, no plan naming it, and no
// cockpit switching on it.
//
// The union being a SUPERSET is deliberate and must stay legal: an unregistered type is dropped by
// EventSource with no error, so it has to be listed before its emitter lands. What was missing is
// the distinction between "early" and "invented". `AWAITING_EMITTER` names the former; this holds
// everything else to a real emitter.
//
// 🪤 TWO emit shapes, and a rail that knew only the first would flag four LIVE events. Most kinds
// are a literal in a `publish(...)` call (`ctx.publish(cid, "phase_advance", …)`), but
// `breaker_trip`, `steering`, `judge_verdict` and `judge_divergence` are ledger kinds — declared as
// constants in `ledger/kinds.py` and written through the journal, so the event name never appears
// inside a `publish(` call at all. Accepting only the literal form would have reported them as dead
// and invited exactly the wrong "cleanup".
describe('every RUN_LIFECYCLE member has a real emitter', () => {
  const PY_ROOT = join(process.cwd(), '..', 'src', 'personalclaw')

  function pythonSources(dir: string): string[] {
    const out: string[] = []
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, entry.name)
      if (entry.isDirectory()) out.push(...pythonSources(p))
      else if (entry.name.endsWith('.py')) out.push(p)
    }
    return out
  }

  const sources = pythonSources(PY_ROOT).map((p) => readFileSync(p, 'utf8'))
  const corpus = sources.join('\n')

  it('reads the Python tree (not vacuously green)', () => {
    // Without this, a bad path would make every "is it emitted?" answer below trivially false —
    // and the allowlist assertion trivially true.
    expect(sources.length).toBeGreaterThan(200)
    expect(corpus).toContain('"phase_advance"')
  })

  /** Backed = a literal inside a `publish(...)` call, OR a ledger-kind constant of that value. */
  function isBacked(kind: string): boolean {
    const literalInPublish = new RegExp(`publish\\((?:[^()]|\\([^()]*\\))*?"${kind}"`)
    const ledgerKind = new RegExp(`^[A-Z_]+ = "${kind}"$`, 'm')
    return literalInPublish.test(corpus) || ledgerKind.test(corpus)
  }

  it('🔑 no member is a name with nothing behind it', () => {
    const unbacked = RUN_LIFECYCLE.filter((k) => !isBacked(k) && !AWAITING_EMITTER.includes(k))
    expect(unbacked, `registered with no emitter and not listed in AWAITING_EMITTER: ${unbacked}`)
      .toEqual([])
  })

  it('and cycle_score in particular is gone', () => {
    // The specific regression: it must not come back through either door.
    expect(RUN_LIFECYCLE).not.toContain('cycle_score' as never)
    expect(corpus).not.toContain('cycle_score')
  })

  it('the ledger-kind events are recognized as backed, not flagged as dead', () => {
    // The vacuity floor for `isBacked`'s second branch. If this ever fails, the rail has narrowed
    // to the literal form and is about to recommend deleting four live events.
    for (const kind of ['breaker_trip', 'steering', 'judge_verdict', 'judge_divergence']) {
      expect(isBacked(kind), `${kind} reads as unbacked`).toBe(true)
    }
  })

  it('AWAITING_EMITTER shrinks — an entry whose emitter landed must graduate', () => {
    // Keeps the allowlist from rotting into a permanent exemption: the moment a publish site
    // appears, the name has to leave this list.
    const graduated = AWAITING_EMITTER.filter((k) => isBacked(k))
    expect(graduated, `now emitted, so remove from AWAITING_EMITTER: ${graduated}`).toEqual([])
  })

  it('AWAITING_EMITTER only names real union members', () => {
    const strays = AWAITING_EMITTER.filter((k) => !RUN_LIFECYCLE.includes(k))
    expect(strays).toEqual([])
  })

  it('🪤 the allowlist is PINNED, so it cannot legalize the next invented name', () => {
    // Without this the exemption list is self-certifying: adding a name to BOTH the union and
    // `AWAITING_EMITTER` satisfies every rail above, which is precisely the hole `cycle_score` fell
    // through — a name with nothing behind it, indistinguishable from one waiting on a known plan.
    // Measured: that two-line mutant passed all six other tests here.
    //
    // Pinned by exact membership rather than a count, following this repo's shrink-only ratchets
    // (`typeRoleAdoption`, `tokenLint.allowlist.json`): an entry may LEAVE freely — that is the
    // graduation the test above requires — but adding one means editing this expectation, which
    // puts the new name in the diff next to the reason it needs an exemption.
    //
    // Reviewing an addition: demand the plan that owes the publish site, and that the consuming
    // surface already folds the event. If neither exists, the name is not early — it is invented.
    expect([...AWAITING_EMITTER].sort()).toEqual([
      'confirmation', 'demotion', 'plan_streaming', 'revision',
    ])
  })
})

// ── WORK-CONTAINERS §6.3 R10c (WF2WOR-7): the coexistence mirror ──
//
// A legacy loop can now RUN as a template, and the backend mirrors that run's events onto the
// equivalent `loop:<id>` hub (`workflows/watchdog._publish_to_equivalent_loop_hub`). So this hub
// carries `workflow_*` frames now. An unregistered type is dropped by EventSource with no error,
// which for a mirror means: connects, delivers, discarded — indistinguishable from never arriving.
describe('the mirrored workflow-run events are registered', () => {
  it('carries every event the workflow engine publishes', () => {
    // Kept in step with `WORKFLOW_LIFECYCLE` in useWorkflowStream.ts: the mirror forwards whatever
    // the engine published, so anything that union lists can land on this hub too.
    for (const ev of [
      'workflow_run_update', 'workflow_node_started', 'workflow_node_done', 'workflow_attention',
      'workflow_needs_input', 'workflow_gate_resolved', 'workflow_gate_revised',
      'workflow_spec_updated', 'workflow_mutation_rejected', 'workflow_forked',
      'workflow_progress', 'workflow_task_materialized', 'workflow_confirmation_pending',
      'workflow_confirmation_resolved', 'workflow_task_verified', 'workflow_cascade_blocked',
      'workflow_steering_consumed',
      'workflow_loop_converged',
    ] as const) {
      expect(RUN_LIFECYCLE).toContain(ev)
    }
  })

  it('stays in step with the workflow hook it mirrors', async () => {
    // The stronger form of the test above: derived from the other union rather than a hand-copied
    // list, so a NEW engine event added there cannot be silently missing here.
    const { WORKFLOW_LIFECYCLE } = await import('../workflows/useWorkflowStream')
    const missing = WORKFLOW_LIFECYCLE.filter((e) => !RUN_LIFECYCLE.includes(e as never))
    expect(missing).toEqual([])
  })
})

describe('the coalesced batch frame is unwrapped on this hook too', () => {
  it('replays members in order, so a fold is identical batched or not', () => {
    const out = unwrapRunBatch({
      events: [
        { event: 'workflow_node_started', payload: { n: 1 } },
        { event: 'workflow_node_done', payload: { n: 2 } },
      ],
    })
    expect(out.map((m) => m.event)).toEqual(['workflow_node_started', 'workflow_node_done'])
    expect(out[1].data).toEqual({ n: 2 })
  })

  it('drops an unrecognized member rather than casting it', () => {
    // The switch would ignore it anyway; a cast would lie about the type.
    const out = unwrapRunBatch({ events: [{ event: 'not_a_real_event', payload: {} }] })
    expect(out).toEqual([])
  })

  it('survives a malformed frame instead of throwing into the listener', () => {
    expect(unwrapRunBatch(null)).toEqual([])
    expect(unwrapRunBatch({ events: 'nope' })).toEqual([])
  })
})
