import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { WorkflowIntrospection } from '../../lib/api'
import { IntrospectPanel, riskyText, rowSummary } from './IntrospectPanel'
import { runCostText } from '../../lib/runCost'

// ── WF2WOR-7 criteria 6 & 8: the nine questions are answerable from the cockpit ──
//
// The gap this closes was not arithmetic — `workflows/introspection.py` was fully tested. It was
// that NOTHING consumed it: no route, no surface. So the properties worth pinning here are about
// CONSUMPTION: that each of the nine questions reaches the DOM, that an empty answer still reads
// as an answer, and that a named backend gap is shown rather than swallowed.

let introspect: (id: string) => Promise<WorkflowIntrospection>

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: { ...actual.api, workflowRunIntrospect: (id: string) => introspect(id) },
  }
})

function payload(over: Partial<WorkflowIntrospection> = {}): WorkflowIntrospection {
  const stats = {
    run_id: 'r1', tokens: 1200, cached_tokens: 100, cost_usd: 0.0342,
    steps_completed: 4, steps_failed: 1, steps_cached: 1, duration_secs: 92.5,
    first_byte_ms: 830, models: ['claude-sonnet'], unverified_steps: 3,
    verification_debt: 0.75, cache_hit_rate: 0.2,
  }
  const proof = {
    summary: '4 steps completed, 1 failed, 1 served from cache',
    verified_steps: 1, total_steps: 4, coverage: 0.25,
    // The caveat string VERBATIM from `proof_section` — the fixture matching production text is
    // what makes this a test of the panel rather than of a paraphrase.
    evidence_files: [],
    warnings: ['no evidence files were captured, so this section is a claim about the run rather than proof of it'],
    honest: true,
  }
  return {
    run_id: 'r1', workflow: 'weekly-report', stats,
    gates: {},
    edges: { branches: {}, judges: {} },
    template_card: {
      template: 'weekly-report', runs: 12, cost_p50: 0.03, cost_p95: 0.09,
      duration_p50: 90, duration_p95: 240, failure_rate: 0.25, warnings: [],
    },
    proof,
    timeline: [
      { kind: 'run_started', ts: '2026-08-11T02:00:00+00:00', node_id: '', instance_path: '', state: '', model: '', detail: '' },
      { kind: 'step_completed', ts: '2026-08-11T02:00:10+00:00', node_id: 'draft', instance_path: 'main.draft', attempt: 2, state: 'done', duration_secs: 8, tokens: 900, cost_usd: 0.02, model: 'claude-sonnet', detail: '' },
    ],
    answers: {
      running: { status: 'complete', workflow: 'weekly-report', nodes: [] },
      changed: [],
      blocked: [],
      approval: [],
      failed: [{ node_id: 'publish' }],
      cost: stats,
      risky: { degraded: [], gates: [], edges: { branches: {}, judges: {} }, verification_debt: 0.75 },
      next: { action: 'nothing', detail: 'this run is complete', queued: [] },
      proof,
    },
    touched: [],
    checklist_gaps: [],
    ...over,
  }
}

beforeEach(() => {
  introspect = async () => payload()
})

describe('the nine questions reach the DOM', () => {
  it('renders every checklist question', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    // Each question is the label of its answer. All nine, because "eight of nine" is precisely
    // the hole the checklist exists to name.
    for (const q of [
      /what is running now/i,
      /what changed/i,
      /what is blocked/i,
      /what needs my approval/i,
      /what failed/i,
      /what is costing money/i,
      /what is risky/i,
      /what happens next if I say nothing/i,
      /were the checks that passed real checks/i,
    ]) {
      expect(await screen.findByText(q)).toBeTruthy()
    }
  })

  it('shows the cost and latency strip with first-byte SEPARATE from duration', async () => {
    // Merging them would conflate a slow start with slow work — one is what a watching user
    // feels, the other is what a scheduler budgets.
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    // `~$`, not `$` (MRT-3): the strip and the "what is costing money" answer render the SAME
    // rate-table-derived number, so both carry the estimate marker or the panel contradicts itself.
    expect(await screen.findByText('~$0.0342')).toBeTruthy()
    expect(screen.getByText(/to first output/i)).toBeTruthy()
    expect(screen.getByText('830 ms')).toBeTruthy()
  })

  it('shows the template p50/p95 card, never a mean', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/cost p50/i)).toBeTruthy()
    expect(screen.getByText(/cost p95/i)).toBeTruthy()
    expect(screen.getByText(/across 12 runs/i)).toBeTruthy()
    // A mean would hide both the typical case and the bad one.
    expect(screen.queryByText(/average|mean/i)).toBeNull()
  })

  it('says a single-run card IS that run rather than implying a distribution', async () => {
    introspect = async () => payload({
      template_card: { template: 't', runs: 1, cost_p50: 0.01, cost_p95: 0.01, duration_p50: 5, duration_p95: 5, failure_rate: 0, warnings: [] },
    })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/p50 and p95 are that one run/i)).toBeTruthy()
  })

  it('answers "what happens next if I say nothing" in words', async () => {
    // The one question no other surface answers, and the one that decides whether a user can
    // walk away. An empty string here would render as a question with no answer.
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText('this run is complete')).toBeTruthy()
  })
})

describe('an empty answer is still an answer', () => {
  it('states the healthy case in words instead of collapsing', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    // Blank space would make a healthy idle run look like a surface that failed to load.
    expect(await screen.findByText('Nothing is blocked')).toBeTruthy()
    expect(screen.getByText('Nothing is waiting on you')).toBeTruthy()
  })

  // ── The five ANSWER sentences, which nothing asserted before ──────────────────────────────
  //
  // 🪤 `riskyText` is a pure function and was the only composed prose this file checked. The panel
  // ALSO builds five sentences inline — nodes active, journal events, nodes blocked, nodes failed,
  // gates never rejecting — and every one hedged its noun with no rail watching. 54 render calls in
  // this file and not one of them read those counts.
  //
  // Asserted on BOTH sides of the boundary, for the reason the degraded/gate pair above proves: a
  // hedge and a correct plural are indistinguishable at every value except 1.
  const one = {
    running: { status: 'running', workflow: 'weekly-report', nodes: [{ node_id: 'draft' }] },
    changed: [], blocked: [{ node_id: 'wait' }], approval: [], failed: [{ node_id: 'publish' }],
  }
  const many = {
    running: { status: 'running', workflow: 'weekly-report', nodes: [{ node_id: 'a' }, { node_id: 'b' }] },
    changed: [], blocked: [{ node_id: 'w1' }, { node_id: 'w2' }], approval: [],
    failed: [{ node_id: 'p1' }, { node_id: 'p2' }, { node_id: 'p3' }],
  }

  it('the answer sentences read SINGULAR at one', async () => {
    const base = payload()
    introspect = async () => ({
      ...base,
      timeline: base.timeline.slice(0, 1),
      answers: { ...base.answers, ...one },
    })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/1 node active/)).toBeTruthy()
    expect(screen.getByText(/1 journal event — see Timeline/)).toBeTruthy()
    expect(screen.getByText(/1 node waiting on something external/)).toBeTruthy()
    expect(screen.getByText(/^1 node failed$/)).toBeTruthy()
  })

  it('and PLURAL above one, with no hedge left anywhere on the panel', async () => {
    const base = payload()
    introspect = async () => ({ ...base, answers: { ...base.answers, ...many } })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/2 nodes active/)).toBeTruthy()
    expect(screen.getByText(/2 journal events — see Timeline/)).toBeTruthy()
    expect(screen.getByText(/2 nodes waiting on something external/)).toBeTruthy()
    expect(screen.getByText(/^3 nodes failed$/)).toBeTruthy()
    // 🪤 Scoped to the panel's OWN prose: the fixture's `proof.summary` is a BACKEND string this
    // surface renders verbatim by design (see the edge-decision test's note), and it still carries
    // `4 step(s)`. Asserting over the whole body would fail on copy this repo does not own here.
    const own = [...document.querySelectorAll('[data-type], p, span')]
      .map((e) => e.textContent || '')
      .filter((t) => /node|journal event|gate/.test(t))
      .join(' | ')
    expect(own, 'the panel composes no hedged noun of its own').not.toMatch(/\((s|es)\)/)
  })

  // 🪤 AND A MIXED CASE, BECAUSE THE TWO ABOVE CANNOT SEE A SWAPPED COUNT. A mutation that made
  // "nodes failed" read `blocked.length` passed both of them: in the singular fixture every count is
  // 1 and in the plural fixture every count is >1, so the two nouns agree either way and the wrong
  // number is invisible. Only counts on OPPOSITE sides of the boundary pin each sentence to its own
  // number — the same mixed-case reasoning the roll-back dialogs needed ("1 of 2 files" beside "the
  // 1 file you picked").
  it('each answer takes ITS OWN count, not a neighbour of the same grammatical number', async () => {
    const base = payload()
    introspect = async () => ({
      ...base,
      timeline: base.timeline,                       // 2 -> plural
      answers: {
        ...base.answers,
        running: { status: 'running', workflow: 'weekly-report', nodes: [{ node_id: 'draft' }] },  // 1
        blocked: [{ node_id: 'w1' }, { node_id: 'w2' }, { node_id: 'w3' }],                        // 3
        failed: [{ node_id: 'publish' }],                                                          // 1
        changed: [], approval: [],
      },
    })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    // Four sentences, three distinct counts, two grammatical numbers — a swap between any pair reds.
    expect(await screen.findByText(/1 node active/)).toBeTruthy()
    expect(screen.getByText(/2 journal events — see Timeline/)).toBeTruthy()
    expect(screen.getByText(/3 nodes waiting on something external/)).toBeTruthy()
    expect(screen.getByText(/^1 node failed$/)).toBeTruthy()
  })

  it('riskyText always produces a sentence, including with no risk', () => {
    expect(riskyText(0, 0, 0)).toMatch(/nothing flagged/i)
    // 🔁 Was pinned as `2 node(s) ran degraded`. `riskyText` is a pure function from counts to prose,
    // so BOTH sides of the grammar's boundary are asserted — the hedge and the correct plural differ
    // only at n === 1, so a fixture fixed at 2 could never have told them apart. The singular call on
    // the line below already existed and matched only `/may not be checking/`, leaving its noun free.
    expect(riskyText(1, 0, 0)).toMatch(/1 node ran degraded/)
    expect(riskyText(2, 0, 0)).toMatch(/2 nodes ran degraded/)
    expect(riskyText(0, 1, 0)).toMatch(/1 gate may not be checking/)
    expect(riskyText(0, 3, 0)).toMatch(/3 gates may not be checking/)
    expect(riskyText(0, 0, 0.75)).toMatch(/75% of completed steps are unverified/)
    // And no hedge survives in any combination this function can produce.
    for (const [d, f] of [[0, 0], [1, 0], [2, 0], [0, 1], [0, 3], [1, 1], [4, 7]]) {
      expect(riskyText(d, f, 0.5), `riskyText(${d}, ${f})`).not.toMatch(/\((s|es)\)/)
    }
  })
})

describe('the said-no fake-check badge', () => {
  it('renders the badge and the backend warning verbatim', async () => {
    // Verbatim because the SAMPLE rule that earns the warning lives in the backend; a second
    // phrasing here would drift from the rule that fired.
    const warning = '`review` passed 40/40 times and has never rejected — a 100% pass rate over this many runs is evidence it is not checking'
    introspect = async () => payload({
      gates: { review: { node_id: 'review', passes: 40, rejects: 0, retries_consumed: 0, total: 40, pass_rate: 1, fake_check_warning: warning } },
    })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/never said no/i)).toBeTruthy()
    expect(screen.getByText(warning)).toBeTruthy()
  })

  it('shows NO badge for a gate with too small a sample', async () => {
    // "0 rejections in 0 runs" and "0 in 40" are different claims. A badge on the third run of a
    // new template teaches the user to ignore badges before the metric was ever right.
    introspect = async () => payload({
      gates: { review: { node_id: 'review', passes: 2, rejects: 0, retries_consumed: 0, total: 2, pass_rate: 1, fake_check_warning: '' } },
    })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    await screen.findByText('review')
    expect(screen.queryByText(/never said no/i)).toBeNull()
  })
})

describe('the edge-decision distribution (PP-8)', () => {
  it('renders a branch case distribution with its dead-case and degenerate warnings verbatim', async () => {
    // Verbatim, for the same reason as the said-no badge: the SAMPLE rule that earns each string
    // lives in the backend, and a second phrasing here would drift from the rule that fired.
    const degenerate =
      '`router` routed to `bug` in all 12 runs that reached it — its one other case is declared but never chosen, so the selector is doing no work'
    introspect = async () =>
      payload({
        edges: {
          branches: {
            router: {
              path: 'router', cases: { bug: 12, feat: 0 }, routed_runs: 12,
              never_taken: ['feat'], degenerate_warning: degenerate,
            },
          },
          judges: {},
        },
      })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText('router')).toBeTruthy()
    expect(screen.getByText(degenerate)).toBeTruthy()
    expect(screen.getByText(/Never taken: feat/)).toBeTruthy()
    expect(screen.getByText(/does no work/i)).toBeTruthy()
  })

  it('renders a degenerate judge verdict distribution', async () => {
    const warning =
      '`grader` returned `pass` on all 12 verdicts — a judge with one outcome over this many calls is not discriminating'
    introspect = async () =>
      payload({
        edges: {
          branches: {},
          judges: { grader: { node_id: 'grader', verdicts: { pass: 12 }, total: 12, degenerate_warning: warning } },
        },
      })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText('grader')).toBeTruthy()
    expect(screen.getByText(warning)).toBeTruthy()
    expect(screen.getByText(/one verdict/i)).toBeTruthy()
  })

  it('states the empty case in words when a template has no edges', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/no branch or judge edges/i)).toBeTruthy()
  })
})

describe('the Proof section states its own caveats', () => {
  it('renders the caveat as prominently as the numbers', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    fireEvent.click(await screen.findByRole('tab', { name: /proof/i }))
    expect(await screen.findByText(/a claim about the run rather than proof of it/i)).toBeTruthy()
    expect(screen.getByText('1 of 4')).toBeTruthy()
  })

  it('flags a section that is neither evidenced nor caveated', async () => {
    // A Proof section with no evidence and no warning is the worst possible surface, because it
    // looks like proof. The backend guarantees one or the other; saying so here makes a
    // regression visible instead of invisible.
    const dishonest = { summary: 'done', verified_steps: 0, total_steps: 0, coverage: 0, evidence_files: [], warnings: [], honest: false }
    introspect = async () => payload({ proof: dishonest, answers: { ...payload().answers, proof: dishonest } })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    fireEvent.click(await screen.findByRole('tab', { name: /proof/i }))
    expect(await screen.findByText(/proves nothing/i)).toBeTruthy()
  })
})

describe('the timeline is the journal AND the attempt ledger', () => {
  it('renders events oldest-first and marks a retry', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    fireEvent.click(await screen.findByRole('tab', { name: /timeline/i }))
    const rows = await screen.findAllByRole('listitem')
    // Oldest-first: newest-first would make a reader reconstruct causality backwards.
    expect(rows[0].textContent).toContain('run_started')
    // Attempt 2+ is a retry — the number that explains a cost the step count alone does not.
    expect(screen.getByText('attempt 2')).toBeTruthy()
  })

  it('rowSummary never renders undefined when a field is absent', () => {
    expect(rowSummary({ kind: 'step_started', ts: '', node_id: '', instance_path: '', state: '', model: '', detail: '' })).toBe('step_started')
    expect(rowSummary({ kind: 'k', ts: '', node_id: '', instance_path: '', state: 'done', model: '', detail: '' })).toBe('done')
  })
})

describe('a named gap is shown, not swallowed', () => {
  it('renders the backend checklist_gaps rather than blank space', async () => {
    // A gap is a BACKEND hole this panel cannot close by rendering harder. Hiding it would make
    // an incomplete surface look complete — the exact thing R6 is against.
    introspect = async () => payload({ checklist_gaps: ['next: what will happen next if I say nothing'] })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/1 of 9 questions cannot be answered/i)).toBeTruthy()
    expect(screen.getByText(/next: what will happen next/i)).toBeTruthy()
  })

  it('shows no gap banner when the payload answers all nine', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    await screen.findByText(/what is running now/i)
    expect(screen.queryByText(/cannot be answered/i)).toBeNull()
  })
})

describe('failures are surfaced, not swallowed', () => {
  it('renders a read failure instead of an empty panel', async () => {
    introspect = async () => { throw new Error('journal unreadable') }
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('journal unreadable')).toBeTruthy())
  })
})

describe('the live touched-items feed', () => {
  it('lists what the run published and what was handed in', async () => {
    introspect = async () => payload({
      touched: [
        { kind: 'artifact', ref: 'report', label: 'Weekly report', action: 'version', detail: '18% changed', node_id: 'write', ts: '2026-08-11T02:00:00+00:00' },
        { kind: 'file', ref: 'input.csv', label: 'input.csv', action: 'dropped', detail: 'text/csv', node_id: '', ts: '2026-08-11T01:00:00+00:00' },
      ],
    })
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    fireEvent.click(await screen.findByRole('tab', { name: /timeline/i }))
    expect(await screen.findByText('Weekly report')).toBeTruthy()
    expect(screen.getByText('input.csv')).toBeTruthy()
    // The publish VERB survives to the DOM: a converged republish is not a new version.
    expect(screen.getByText('version')).toBeTruthy()
  })

  it('renders no Touched section when the run touched nothing', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    fireEvent.click(await screen.findByRole('tab', { name: /timeline/i }))
    expect(screen.queryByText('Touched')).toBeNull()
  })
})

// ── MRT-3: the run's money line says it is an estimate ──
//
// The atom's clause is `~$X this run`. The shipped line was `$${cost.toFixed(4)} this run` —
// four decimals of precision on a figure `pricing.py` derives from a static price table. These
// pin the disclosure, not the arithmetic: a money surface that reads as exact is the defect.

describe('the run cost line', () => {
  it('marks a derived cost as an estimate and never renders it as exact', () => {
    const text = runCostText(0.1234)
    expect(text).toContain('~$0.1234')
    expect(text).toContain('estimated from model prices')
    expect(text).toContain('not a provider-reported charge')
    // The tilde is the point: no leading bare "$0.1234".
    expect(text.startsWith('$')).toBe(false)
  })

  it('rounds to cents once there is a dollar, matching the Usage panel', () => {
    expect(runCostText(4.2)).toContain('~$4.20')
    // …and keeps four decimals below a dollar, so a real $0.0012 is not "$0.00".
    expect(runCostText(0.0012)).toContain('~$0.0012')
  })

  it('does not claim $0.00 when nothing was recorded', () => {
    for (const zero of [0, -0, Number.NaN]) {
      const text = runCostText(zero)
      expect(text).not.toContain('$')
      expect(text).toContain('no price row')
    }
  })

  it('reaches the DOM as the answer to "what is costing money"', async () => {
    render(<IntrospectPanel runId="r1" onClose={() => {}} />)
    expect(await screen.findByText(/~\$.* this run/)).toBeTruthy()
  })
})
