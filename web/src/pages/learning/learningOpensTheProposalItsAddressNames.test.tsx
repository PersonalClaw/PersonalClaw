// @vitest-environment jsdom
import { describe, expect, it, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import { LearningPage } from './LearningPage'
import type { LearningGate, LearningInbox, LearningRow, StagingWeek } from '../../lib/api'

// ── Learning opens the proposal its address names ──────────────────────────────────────────────
//
// Needs you's panel ends with "Open in Learning", `#/learning?open=<id>`: the page scrolls that
// proposal into view and moves the focus to it, so the link lands on the thing it named. And a
// row's chips say their detail in the themed tip and to a screen reader, never in a native title.

const GATE_UNGATED: LearningGate = {
  state: 'ungated',
  reason: 'no gate run yet — accept on the evidence above, or run the gate first',
  before: null, after: null, delta: null, regressed: false, scenarios: 0,
  halted: false, dollars_est: 0, unpriced_attempts: 0, spend_observed: false, pin: {}, ran_at: '',
}

const gated = (over: Partial<LearningGate> = {}): LearningGate => ({
  state: 'gated', reason: '',
  before: 0.9, after: 0.4, delta: -0.5, regressed: true, scenarios: 12,
  halted: false, dollars_est: 0.031, unpriced_attempts: 0, spend_observed: true,
  pin: { model_fp: 'abc123def456', scenario_sha256: 'f00dcafe' },
  ran_at: '2026-08-27T10:00:00+00:00',
  ...over,
})

const row = (over: Partial<LearningRow> = {}): LearningRow => ({
  id: 'skill-deadbeef0001', kind: 'skill',
  title: 'Promote the release checklist we re-derived three times',
  provenance: 'inferred', source_cadence: 'skill_promotion', source_excerpt: '',
  evidence_refs: ['run-1'], evidence_strength: 'correlated', reinforcements: 1, confidence: 0.4,
  manifest_valid: true, manifest_issues: [], risk_tier: 'review',
  status: 'pending', renderable: true, bulk_acceptable: true,
  gate: GATE_UNGATED,
  replay: {
    state: 'unreplayed', reason: 'no replay run yet', verdict: 'unmeasured',
    candidate_mean: null, baseline_mean: null, cases: 0, scored: 0, rejected: 0, tool_free: 0,
    deferred: false, provenance: [], ran_at: '',
  },
  ...over,
})

const inboxOf = (rows: LearningRow[]): LearningInbox => ({
  rows, total: rows.length, by_kind: {}, by_tier: {},
  flagged: 0, unrenderable: [], bulk_acceptable: rows.length,
})

const WEEK: StagingWeek = {
  days: 7, buckets: [], silent_days: [], error_days: [], produced_total: 0, cost_usd: 0,
}

const learningProposals = vi.fn<() => Promise<LearningInbox>>()
const learningStagingWeek = vi.fn<() => Promise<StagingWeek>>()
const learningHealth = vi.fn<() => Promise<never>>()
const judgeBench = vi.fn<() => Promise<{ ran: false }>>()
const evalStudies = vi.fn<() => Promise<never>>()
const retrievalBench = vi.fn<() => Promise<{ ran: false }>>()
const ablation = vi.fn<() => Promise<{ ran: false }>>()
const acceptLearningProposal = vi.fn((_id: string) => Promise.resolve({ ok: true }))

// PARTIAL mock via `importOriginal`, for the reason `evidenceGrade.test.tsx` records: the five side
// panels branch on the REAL `isSwitchedOff`/`isNotRun`, so a factory returning only `api` throws from inside the
// render and every assertion below would die before it ran.
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      // LearningPage now also reads attention accounting; empty is its ordinary
      // state, and an omitted stub would throw inside a passive effect (see note above).
      workflowAttention: () => Promise.resolve({ scopes: [] }),
      evalFieldMetrics: () => Promise.resolve({ subjects: [] }),
      learningProposals: () => learningProposals(),
      learningStagingWeek: () => learningStagingWeek(),
      learningHealth: () => learningHealth(),
      acceptLearningProposal: (id: string) => acceptLearningProposal(id),
      rejectLearningProposal: () => Promise.resolve(undefined),
      judgeBench: () => judgeBench(),
      evalStudies: () => evalStudies(),
      retrievalBench: () => retrievalBench(),
      ablation: () => ablation(),
      identityReport: () => Promise.reject(new Error('not under test')),
      learningBenchmark: () => Promise.resolve({ ran: false }),
    },
  }
})


beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  sessionStorage.clear()
  learningStagingWeek.mockResolvedValue(WEEK)
  learningHealth.mockRejectedValue(new Error('not under test'))
  judgeBench.mockResolvedValue({ ran: false })
  evalStudies.mockRejectedValue(new Error('not under test'))
  retrievalBench.mockResolvedValue({ ran: false })
  ablation.mockResolvedValue({ ran: false })
})

describe('an address that names a proposal', () => {
  it('scrolls it into view and moves the focus to it', async () => {
    learningProposals.mockResolvedValue(inboxOf([row({ id: 'lp-a', title: 'First' }), row({ id: 'lp-b', title: 'The named one' })]))
    const into = vi.fn()
    Element.prototype.scrollIntoView = into
    render(<LearningPage navigate={() => {}} query={{ open: 'lp-b' }} />)
    await screen.findByText('The named one')
    const named = document.querySelector('[data-learning-proposal="lp-b"]') as HTMLElement
    expect(document.activeElement).toBe(named)
    expect(into).toHaveBeenCalled()
  })
})

describe('a row’s chips', () => {
  it('carry no native title; a manifest chip’s issues are read with it', async () => {
    learningProposals.mockResolvedValue(inboxOf([row({ id: 'lp-a', title: 'Drops the score', gate: gated(), manifest_valid: false, manifest_issues: ['no target'] })]))
    render(<LearningPage navigate={() => {}} />)
    await screen.findByText('score drop')
    expect(document.querySelectorAll('[data-learning-proposal] [title]')).toHaveLength(0)
    expect(screen.getByText('manifest').closest('span')!.textContent).toContain(': no target')
  })
})
