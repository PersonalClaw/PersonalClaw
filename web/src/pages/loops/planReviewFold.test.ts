import { describe, it, expect } from 'vitest'
import { planReviewReducer, emptyPlanReview } from './planReviewFold'

describe('planReviewReducer — plan_streaming', () => {
  it('appends chunks into the buffer and holds names', () => {
    let s = emptyPlanReview()
    s = planReviewReducer(s, 'plan_streaming', { chunk: '{"steps":[' })
    s = planReviewReducer(s, 'plan_streaming', { chunk: '{"id":"a"}]}', done: true, names: { title: 'T' } })
    expect(s.buffer).toBe('{"steps":[{"id":"a"}]}')
    expect(s.complete).toBe(true)
    expect(s.names?.title).toBe('T')
  })

  it('a buffer field REPLACES (a resync), not appends', () => {
    let s = planReviewReducer(emptyPlanReview(), 'plan_streaming', { chunk: 'stale' })
    s = planReviewReducer(s, 'plan_streaming', { buffer: '{"steps":[]}' })
    expect(s.buffer).toBe('{"steps":[]}')
  })

  it('ignores a chunk-less, buffer-less event (keeps state)', () => {
    const s0 = planReviewReducer(emptyPlanReview(), 'plan_streaming', { chunk: 'x' })
    const s1 = planReviewReducer(s0, 'plan_streaming', {})
    expect(s1.buffer).toBe('x')
  })
})

describe('planReviewReducer — revision relabels only changed steps', () => {
  it('merges revision labels over the running set', () => {
    let s = planReviewReducer(emptyPlanReview(), 'plan_streaming', {
      names: { labels: { a: 'A', b: 'B' } },
    })
    s = planReviewReducer(s, 'revision', { labels: { b: 'B-revised' } })
    expect(s.names?.labels).toEqual({ a: 'A', b: 'B-revised' }) // a untouched
  })

  it('a re-streamed buffer on revision re-opens the plan (not complete until done)', () => {
    let s = planReviewReducer(emptyPlanReview(), 'plan_streaming', { chunk: '{}', done: true })
    expect(s.complete).toBe(true)
    s = planReviewReducer(s, 'revision', { buffer: '{"steps":[]}' })
    expect(s.complete).toBe(false)
  })
})

describe('planReviewReducer — confirmation gate', () => {
  it('a prompt opens the gate; a resolve closes it', () => {
    let s = planReviewReducer(emptyPlanReview(), 'confirmation', { prompt: 'Ready?' })
    expect(s.confirmation).toBe('Ready?')
    s = planReviewReducer(s, 'confirmation', { done: true })
    expect(s.confirmation).toBeNull()
  })

  it('defaults a promptless open to a sensible message', () => {
    expect(planReviewReducer(emptyPlanReview(), 'confirmation', {}).confirmation).toContain('Confirm')
  })
})

describe('planReviewReducer — demotion', () => {
  it('records the reason for the per-stage-approval banner', () => {
    const s = planReviewReducer(emptyPlanReview(), 'demotion', { reason: 'judge low-confidence' })
    expect(s.demotedReason).toBe('judge low-confidence')
  })

  it('defaults a reasonless demotion', () => {
    expect(planReviewReducer(emptyPlanReview(), 'demotion', {}).demotedReason).toBeTruthy()
  })
})

describe('planReviewReducer — purity + unknown events', () => {
  it('never mutates the input state', () => {
    const s0 = emptyPlanReview()
    const s1 = planReviewReducer(s0, 'demotion', {})
    expect(s0.demotedReason).toBeNull()
    expect(s1).not.toBe(s0)
  })

  it('ignores an unrelated lifecycle event', () => {
    const s = planReviewReducer(emptyPlanReview(), 'new_finding', {})
    expect(s).toEqual(emptyPlanReview())
  })
})
