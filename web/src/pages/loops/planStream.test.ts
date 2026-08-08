import { describe, it, expect } from 'vitest'
import { parsePartialJson, toPlanDraft, reparseBuffer } from './planStream'

describe('parsePartialJson — best-effort parse of a growing buffer', () => {
  it('parses a complete document unchanged (fast path)', () => {
    expect(parsePartialJson('{"title":"T","steps":[{"id":"a"}]}')).toEqual({
      title: 'T', steps: [{ id: 'a' }],
    })
  })

  it('repairs an object truncated mid-value', () => {
    const parsed = parsePartialJson('{"title":"Build the thing","steps":[{"id":"a"},{"id":"b"')
    expect(parsed).toEqual({ title: 'Build the thing', steps: [{ id: 'a' }, { id: 'b' }] })
  })

  it('drops a dangling key that has no value yet', () => {
    const parsed = parsePartialJson('{"title":"T","desc') as Record<string, unknown>
    expect(parsed).toEqual({ title: 'T' })
  })

  it('closes an array truncated after a trailing comma', () => {
    expect(parsePartialJson('{"steps":[{"id":"a"},')).toEqual({ steps: [{ id: 'a' }] })
  })

  it('closes an open string', () => {
    const parsed = parsePartialJson('{"title":"Half a titl') as Record<string, unknown>
    expect(parsed.title).toBe('Half a titl')
  })

  it('returns null for an unrepairable fragment', () => {
    expect(parsePartialJson('not json at all {[')).toBeNull()
    expect(parsePartialJson('')).toBeNull()
  })
})

describe('toPlanDraft — coerce parsed plan into the render shape', () => {
  it('accepts a bare {steps} and a wrapped {plan:{steps}}', () => {
    const bare = toPlanDraft({ title: 'T', steps: [{ id: 'a', title: 'A' }] })
    expect(bare.title).toBe('T')
    expect(bare.steps).toEqual([{ id: 'a', label: 'A', role: undefined, kind: undefined, target: undefined, depends_on: undefined, pending: true }])
    const wrapped = toPlanDraft({ plan: { steps: [{ id: 'a' }] } })
    expect(wrapped.steps).toHaveLength(1)
  })

  it('drops steps with no id (a nameless in-flight node)', () => {
    const d = toPlanDraft({ steps: [{ id: 'a' }, { role: 'x' }, { id: '' }] })
    expect(d.steps.map((s) => s.id)).toEqual(['a'])
  })

  it('marks the last step pending while streaming, nothing pending once complete', () => {
    const streaming = toPlanDraft({ steps: [{ id: 'a' }, { id: 'b' }] })
    expect(streaming.steps.map((s) => s.pending)).toEqual([false, true])
    const done = toPlanDraft({ steps: [{ id: 'a' }, { id: 'b' }] }, { complete: true })
    expect(done.steps.every((s) => !s.pending)).toBe(true)
  })

  it('reads node_id / objective aliases', () => {
    const d = toPlanDraft({ steps: [{ node_id: 'n1', objective: 'do it' }] })
    expect(d.steps[0]).toMatchObject({ id: 'n1', target: 'do it' })
  })
})

describe('reparseBuffer — partial → full transition keeps the last good draft', () => {
  it('a clean parse replaces the draft', () => {
    const r = reparseBuffer('{"steps":[{"id":"a"}]}', null)
    expect(r.parsed).toBe(true)
    expect(r.draft.steps).toHaveLength(1)
  })

  it('an unparseable chunk keeps the last good draft rather than blanking', () => {
    const good = reparseBuffer('{"steps":[{"id":"a"},{"id":"b"}]}', null, { complete: true })
    const bad = reparseBuffer('###garbage###', good.draft)
    expect(bad.parsed).toBe(false)
    expect(bad.draft).toBe(good.draft) // same object — the plan the user was reading stands
  })

  it('grows the plan chunk-by-chunk as the buffer fills', () => {
    let draft = reparseBuffer('{"title":"Ship","steps":[{"id":"plan"', null).draft
    expect(draft.steps.map((s) => s.id)).toEqual(['plan'])
    draft = reparseBuffer('{"title":"Ship","steps":[{"id":"plan"},{"id":"build"}]}', draft, { complete: true }).draft
    expect(draft.title).toBe('Ship')
    expect(draft.steps.map((s) => s.id)).toEqual(['plan', 'build'])
  })
})
