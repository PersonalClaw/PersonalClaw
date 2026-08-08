import { describe, it, expect } from 'vitest'
import { planDepths, layoutPlanDag } from './planGraph'
import type { PlanDraft } from './planStream'

describe('planDepths — dependency depth is the column', () => {
  it('a step with no deps is depth 0; each dependent is one deeper', () => {
    const d = planDepths([
      { id: 'a' },
      { id: 'b', depends_on: ['a'] },
      { id: 'c', depends_on: ['b'] },
    ])
    expect([d.get('a'), d.get('b'), d.get('c')]).toEqual([0, 1, 2])
  })

  it('depth is the MAX over prerequisites (a fan-in lands past its deepest input)', () => {
    const d = planDepths([
      { id: 'a' },
      { id: 'b', depends_on: ['a'] },
      { id: 'c', depends_on: ['a', 'b'] }, // deepest input is b(1) → c is 2
    ])
    expect(d.get('c')).toBe(2)
  })

  it('ignores a missing / forward reference', () => {
    const d = planDepths([{ id: 'a', depends_on: ['ghost'] }])
    expect(d.get('a')).toBe(0)
  })

  it('breaks a dependency cycle instead of hanging', () => {
    const d = planDepths([
      { id: 'a', depends_on: ['b'] },
      { id: 'b', depends_on: ['a'] },
    ])
    expect(d.get('a')).toBeTypeOf('number')
    expect(d.get('b')).toBeTypeOf('number')
  })
})

describe('layoutPlanDag', () => {
  const draft = (over: Partial<PlanDraft> = {}): PlanDraft => ({ steps: [], ...over })

  it('an empty plan yields a zero-size layout (no stray box)', () => {
    expect(layoutPlanDag(draft())).toEqual({ nodes: [], edges: [], width: 0, height: 0 })
  })

  it('a plan with NO declared deps degrades to a straight left-to-right chain', () => {
    const l = layoutPlanDag(draft({ steps: [{ id: 'a' }, { id: 'b' }, { id: 'c' }] }))
    // one node per column (x strictly increasing), chained edges a->b->c
    const xs = l.nodes.map((n) => n.x)
    expect(new Set(xs).size).toBe(3)
    expect(l.edges.map((e) => e.id)).toEqual(['a->b', 'b->c'])
  })

  it('declared deps drive columns + edges', () => {
    const l = layoutPlanDag(draft({
      steps: [{ id: 'a' }, { id: 'b', depends_on: ['a'] }, { id: 'c', depends_on: ['a'] }],
    }))
    // b and c share column 1 (both depend on a) → stacked rows, same x
    const b = l.nodes.find((n) => n.id === 'b')!
    const c = l.nodes.find((n) => n.id === 'c')!
    expect(b.x).toBe(c.x)
    expect(b.y).not.toBe(c.y)
    expect(l.edges.map((e) => e.id).sort()).toEqual(['a->b', 'a->c'])
  })

  it('a pending step renders active; a settled step todo', () => {
    const l = layoutPlanDag(draft({ steps: [{ id: 'a' }, { id: 'b', pending: true }] }))
    expect(l.nodes.find((n) => n.id === 'a')!.state).toBe('todo')
    expect(l.nodes.find((n) => n.id === 'b')!.state).toBe('active')
  })

  it('labels via the provided labeler', () => {
    const l = layoutPlanDag(draft({ steps: [{ id: 'a', label: 'Alpha' }] }), (s) => s.label ?? s.id)
    expect(l.nodes[0].content).toBe('Alpha')
  })
})
