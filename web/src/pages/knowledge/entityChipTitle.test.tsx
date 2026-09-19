import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EntitiesSection, entityChipTitle } from './KnowledgeDetailPage'

// ── An entity chip must be able to explain a link the document never spells out (#1779) ────────
//
// The knowledge graph's deterministic pre-pass links on an ALIAS: a note that only ever writes
// "SPRW" gets a chip reading "Sparrow", and the chip was the surface where that looks like a bug
// rather than the feature. The backend has always serialized `aliases` as a real array
// (`_serialize_entity`) — nothing ever WROTE one, so there was nothing to show and the FE type did
// not even carry the field.
//
// Asserted through a real render, not by reading the source: the point is what a reader can
// actually hover, and the title is composed by a helper precisely so it can be checked directly.

const chips = (entities: Parameters<typeof EntitiesSection>[0]['entities']) => {
  render(<EntitiesSection entities={entities} />)
  return screen.getAllByText(/Sparrow|Kestrel/).map((n) => n.closest('span[title]'))
}

describe('entityChipTitle', () => {
  it('names the aliases the document used, after the type', () => {
    expect(entityChipTitle({ entity_type: 'project', aliases: ['SPRW', '@sparrow'] }))
      .toBe('project · also SPRW, @sparrow')
  })

  it('is just the type when the entity has no alias — the pre-#1779 shape, unchanged', () => {
    expect(entityChipTitle({ entity_type: 'project', aliases: [] })).toBe('project')
    expect(entityChipTitle({ entity_type: 'project' })).toBe('project')
  })

  it('never renders an empty or dangling tooltip', () => {
    // `undefined` (no title attribute at all) rather than "" or "· also" — a tooltip that opens
    // on nothing is worse than none.
    expect(entityChipTitle({})).toBeUndefined()
    expect(entityChipTitle({ aliases: ['  '] })).toBeUndefined()
    expect(entityChipTitle({ aliases: ['SPRW'] })).toBe('also SPRW')
  })
})

describe('the rendered chip', () => {
  it('carries the aliases so a reader can see WHY the item linked here', () => {
    const [chip] = chips([
      { id: 'e1', name: 'Sparrow', entity_type: 'project', aliases: ['SPRW'] },
    ])
    expect(chip, 'the chip rendered at all — positive control').toBeTruthy()
    expect(chip!.getAttribute('title')).toBe('project · also SPRW')
    // The visible label stays the CANONICAL name; aliases are explanation, not a rename.
    expect(chip!.textContent).toContain('Sparrow')
  })

  it('leaves an alias-free entity exactly as it was', () => {
    const [chip] = chips([{ id: 'e2', name: 'Kestrel', entity_type: 'project' }])
    expect(chip!.getAttribute('title')).toBe('project')
  })
})
