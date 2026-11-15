import { describe, expect, it } from 'vitest'
import { ITEM_REF_KINDS, itemRef, pageOfItem, parseItemRef, type ItemRef } from './itemRef'

// ── A library item's ref reads back as the item it names, and nothing else reads as one ─────────
//
// The library's read names each row `<kind>:<id>` (`dashboard/handlers/library.py`), and a link
// that attaches items carries the same words (`#/chat/new?attach=<ref>,<ref>`). One writer and one
// reader, so a ref a page writes is the ref a link reads, and a hand-typed one that names no shape
// the stores use is null rather than a guess at a page or a read.

/** One row of each kind, ids in each store's own shape. */
const ROWS: ItemRef[] = [
  { kind: 'knowledge', id: '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d' },
  { kind: 'knowledge', id: 'k-0a1b2c3d' },
  { kind: 'artifact', id: 'lisbon-weekend' },
  { kind: 'report', id: 'rpt-1a2b3c4d' },
  { kind: 'findings', id: 'cheap-ways-to-cut-the-power-bill' },
]

describe('item refs', () => {
  it('🔑 round-trip for each kind', () => {
    expect(new Set(ROWS.map((r) => r.kind)), 'every kind is covered').toEqual(new Set(ITEM_REF_KINDS))
    for (const row of ROWS) expect(parseItemRef(itemRef(row))).toEqual(row)
    expect(itemRef({ kind: 'artifact', id: 'lisbon-weekend' })).toBe('artifact:lisbon-weekend')
  })

  it('🔑 anything else is null', () => {
    for (const raw of [
      '', 'knowledge', 'knowledge:', ':k1', 'note:k1', 'Knowledge:k1', 'chat:abc', 'artifact: lisbon',
      'artifact:lisbon/edit', 'artifact:lisbon,report:r1', 'artifact:../secrets', 'artifact:-lead',
      'findings:what?view=x', 'knowledge:k1#top', `artifact:${'a'.repeat(201)}`,
    ]) expect(parseItemRef(raw), raw).toBeNull()
    for (const raw of [undefined, null, 42, { kind: 'knowledge', id: 'k1' }]) expect(parseItemRef(raw)).toBeNull()
  })

  it('a ref opens at its own page; a report at its write-up, and what an intent found on its intent', () => {
    expect(pageOfItem({ kind: 'knowledge', id: 'k1' })).toBe('knowledge/item/k1')
    expect(pageOfItem({ kind: 'artifact', id: 'lisbon' })).toBe('artifacts/lisbon')
    expect(pageOfItem({ kind: 'report', id: 'rpt-1' }, '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d'))
      .toBe('knowledge/item/0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d')
    expect(pageOfItem({ kind: 'report', id: 'rpt-1' }, '3f9a0c1d2e4b'), 'the persist step\'s 12-hex id').toBe('knowledge/item/3f9a0c1d2e4b')
    expect(pageOfItem({ kind: 'report', id: 'rpt-1' }), 'no write-up yet: the Reports page').toBe('knowledge/reports')
    expect(pageOfItem({ kind: 'report', id: 'rpt-1' }, 'k-0a1b2c3d'), 'a write-up id the store does not mint').toBe('knowledge/reports')
    expect(pageOfItem({ kind: 'report', id: 'rpt-1' }, 'x/../y'), 'a write-up id of no shape is no page').toBe('knowledge/reports')
    expect(pageOfItem({ kind: 'findings', id: 'power-bill' })).toBe('knowledge?view=intents&intent=power-bill')
  })
})
