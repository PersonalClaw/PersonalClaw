import { describe, it, expect } from 'vitest'
import {
  resolvePlanNames,
  fallbackTitle,
  fallbackDescription,
  fallbackLabel,
} from './planNaming'
import type { PlanDraft } from './planStream'

const draft = (over: Partial<PlanDraft> = {}): PlanDraft => ({
  steps: [
    { id: 'research', label: 'Research sources' },
    { id: 'draft_report', role: 'writer' },
    { id: 'review_step' },
  ],
  ...over,
})

describe('resolvePlanNames — model output authoritative, fallback fills gaps', () => {
  it('uses model names when present', () => {
    const named = resolvePlanNames(draft(), {
      title: 'Market scan',
      description: 'Scan then write.',
      labels: { research: 'Gather', draft_report: 'Write', review_step: 'Review' },
    })
    expect(named.title).toBe('Market scan')
    expect(named.description).toBe('Scan then write.')
    expect(named.labels).toEqual({ research: 'Gather', draft_report: 'Write', review_step: 'Review' })
  })

  it('merges per-field: a model that named the plan but not step 3 still labels step 3', () => {
    const named = resolvePlanNames(draft(), {
      title: 'Market scan',
      labels: { research: 'Gather' },
    })
    expect(named.title).toBe('Market scan')
    expect(named.labels.research).toBe('Gather') // model
    expect(named.labels.draft_report).toBe('writer') // draft field fallback
    expect(named.labels.review_step).toBe('Review step') // humanized-id fallback
  })
})

describe('deterministic floor — NO model input still yields usable names', () => {
  it('resolvePlanNames(draft, null) produces a title, description, and a label per step', () => {
    const named = resolvePlanNames(draft(), null, 'Analyze the Q3 competitor landscape\nand summarize')
    expect(named.title).toBe('Analyze the Q3 competitor landscape')
    expect(named.description).toContain('3 steps')
    expect(Object.keys(named.labels)).toEqual(['research', 'draft_report', 'review_step'])
    expect(named.labels.research).toBe('Research sources')
    expect(named.labels.review_step).toBe('Review step') // never a raw id
    // every label is a non-empty human string
    expect(Object.values(named.labels).every((l) => l.trim().length > 0)).toBe(true)
  })

  it('fallbackTitle: goal first line wins, else a step-count phrase, never empty', () => {
    expect(fallbackTitle(draft(), 'Build a dashboard.')).toBe('Build a dashboard')
    expect(fallbackTitle(draft(), '')).toBe('Plan · 3 steps')
    expect(fallbackTitle({ steps: [] }, '')).toBe('Plan')
    expect(fallbackTitle({ steps: [{ id: 'a' }] }, '')).toBe('Plan · 1 step')
  })

  it('fallbackTitle truncates a long goal line to a headline length', () => {
    const long = 'x'.repeat(120)
    expect(fallbackTitle({ steps: [] }, long).length).toBeLessThanOrEqual(60)
    expect(fallbackTitle({ steps: [] }, long).endsWith('…')).toBe(true)
  })

  it('fallbackDescription names the ordered steps', () => {
    expect(fallbackDescription(draft())).toBe(
      '3 steps: Research sources, writer, then Review step.',
    )
    expect(fallbackDescription({ steps: [{ id: 'only', label: 'One' }] })).toBe('A single step: One.')
    expect(fallbackDescription({ steps: [] })).toBe('No steps yet.')
  })

  it('fallbackLabel: label > role > kind > humanized id', () => {
    expect(fallbackLabel({ id: 'a', label: 'L', role: 'R' })).toBe('L')
    expect(fallbackLabel({ id: 'a', role: 'writer' })).toBe('writer')
    expect(fallbackLabel({ id: 'a', kind: 'run_command' })).toBe('run command')
    expect(fallbackLabel({ id: 'gather_facts' })).toBe('Gather facts')
  })
})
