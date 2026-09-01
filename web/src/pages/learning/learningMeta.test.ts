import { describe, expect, it } from 'vitest'
import {
  DAY_HINT, DAY_TONE, bulkBlockedReason, dayLabel, dayState,
  kindIcon, kindLabel, tierLabel, tierTone,
} from './learningMeta'
import type { LearningRow, StagingDay } from '../../lib/api'

// ── The Learning page's presentation rules (§6.1) ─────────────────────────────
//
// The property that keeps this page honest: every JUDGEMENT arrives from the backend already made —
// ordering, bulk eligibility, renderability. This module only LABELS and EXPLAINS. Re-deriving a rule
// in TS would eventually disagree with the server, and the FE would be the copy shipping the
// permissive answer about what is safe to accept.

const row = (over: Partial<LearningRow> = {}): LearningRow => ({
  id: 'p1', kind: 'template_diff', title: 'add a retry', provenance: 'refiner',
  source_cadence: 'run_end', source_excerpt: 'step_failed x4',
  evidence_refs: ['r1'], evidence_strength: 'correlated', reinforcements: 4, confidence: 0.8,
  manifest_valid: true, manifest_issues: [], risk_tier: 'review',
  status: 'pending', renderable: true, bulk_acceptable: true,
  gate: {
    state: 'ungated', reason: 'no gate run yet', before: null, after: null, delta: null,
    regressed: false, scenarios: 0, halted: false, dollars_est: 0, spend_observed: false,
    pin: {}, ran_at: '',
  },
  replay: {
    state: 'unreplayed', reason: 'no replay run yet', verdict: 'unmeasured',
    candidate_mean: null, baseline_mean: null, cases: 0, scored: 0, rejected: 0, tool_free: 0,
    deferred: false, provenance: [], ran_at: '',
  },
  ...over,
})

const day = (over: Partial<StagingDay> = {}): StagingDay => ({
  day: '2024-01-03', passes: 3, by_outcome: { flush_ok: 3 }, produced: 0,
  errors: 0, staged: 0, cost_usd: 0.01, proposal_ids: [], ...over,
})

describe('kind labels', () => {
  it('names every kind the backend serves', () => {
    for (const kind of [
      'skill', 'lesson_batch', 'template', 'template_diff', 'retirement', 'tier_migration',
      'project_instruction', 'project_file', 'project_skill', 'knowledge_draft',
    ]) {
      expect(kindLabel(kind)).not.toBe(kind)
    }
  })

  it('decodes the internal names a reviewer should never have to read', () => {
    expect(kindLabel('lesson_batch')).toBe('Lessons')
    expect(kindLabel('tier_migration')).toBe('Tier change')
  })

  it('falls back to the raw id rather than an empty chip', () => {
    // A row whose kind cannot be named is still a row that needs deciding.
    expect(kindLabel('brand_new_kind')).toBe('brand_new_kind')
    expect(kindIcon('brand_new_kind')).toBeTruthy()
  })
})

describe('risk tiers', () => {
  it('labels the three the refiner assigns', () => {
    expect(tierLabel('low')).toBe('Low risk')
    expect(tierLabel('review')).toBe('Review')
    expect(tierLabel('manual_only')).toBe('Manual only')
  })

  it('gives an UNSCORED tier the warn tone, matching the backend sort', () => {
    // The server sorts an unscored tier ABOVE manual_only: nobody judged its risk, which is more
    // urgent than a judged destructive edit. A calm tone here would contradict that ordering.
    expect(tierLabel('who-knows')).toBe('Unscored')
    expect(tierTone('who-knows')).toBe(tierTone('manual_only'))
  })
})

describe('bulkBlockedReason', () => {
  it('says nothing when the backend says the row is eligible', () => {
    expect(bulkBlockedReason(row())).toBe('')
  })

  it('explains the flag rather than re-deriving it', () => {
    // Every branch reads `bulk_acceptable: false` — the FE never decides eligibility itself.
    expect(bulkBlockedReason(row({ bulk_acceptable: false, renderable: false }))).toContain('provenance')
    expect(bulkBlockedReason(row({ bulk_acceptable: false, risk_tier: 'manual_only' }))).toContain('destructive')
    expect(bulkBlockedReason(row({ bulk_acceptable: false, manifest_valid: false }))).toContain('manifest')
    expect(bulkBlockedReason(row({ bulk_acceptable: false, evidence_refs: [] }))).toContain('evidence')
  })

  it('trusts the backend flag even when every visible field looks fine', () => {
    // The decisive assertion: if the server said no, the reason is generic but the answer is still no.
    // A version that recomputed from the visible fields would say "eligible" and be wrong.
    expect(bulkBlockedReason(row({ bulk_acceptable: false }))).not.toBe('')
  })

  it('reports unrenderable BEFORE the other reasons', () => {
    // A row that cannot be shown weighably is the most serious of the four, and naming a lesser
    // reason first would send a reader after the wrong fix.
    const blocked = row({ bulk_acceptable: false, renderable: false, manifest_valid: false, evidence_refs: [] })
    expect(bulkBlockedReason(blocked)).toContain('provenance')
  })
})

describe('the capture week panel', () => {
  it('calls a day with no passes SILENT', () => {
    // The whole reason the panel exists: an aggregate view cannot distinguish this from a quiet day.
    expect(dayState(day({ passes: 0 }))).toBe('silent')
    expect(DAY_HINT.silent).toContain('gap')
  })

  it('ranks an error above a production', () => {
    expect(dayState(day({ passes: 3, errors: 1, produced: 2 }))).toBe('error')
    expect(dayState(day({ passes: 3, produced: 2 }))).toBe('produced')
  })

  it('calls a ran-but-quiet day ok, not silent', () => {
    // Ran and produced nothing is healthy; never ran is not. Collapsing them would lose the signal.
    expect(dayState(day({ passes: 4, produced: 0 }))).toBe('ok')
  })

  it('gives every state a distinct tone and a hint', () => {
    const states = ['silent', 'error', 'produced', 'ok'] as const
    expect(new Set(states.map((s) => DAY_TONE[s])).size).toBe(4)
    for (const s of states) expect(DAY_HINT[s]).toBeTruthy()
  })
})

describe('dayLabel', () => {
  it('parses the bucket as LOCAL time', () => {
    // `new Date('2024-01-01')` is parsed as UTC and shifts the weekday a day west of the reader —
    // the backend buckets by local date, so the label has to agree.
    const expected = new Date(2024, 0, 1).toLocaleDateString(undefined, { weekday: 'short' })
    expect(dayLabel('2024-01-01')).toBe(expected)
  })

  it('returns the raw string when the bucket is malformed', () => {
    expect(dayLabel('not-a-date')).toBe('not-a-date')
    expect(dayLabel('')).toBe('')
  })
})
