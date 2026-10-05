import { describe, expect, it } from 'vitest'
import { asSent, markerFor, takeIn, type PasteBlock } from './pasteBlocks'

// ── The one place a marker becomes its paste, and the one way a sent message comes back ────────

const block = (seq: number, content: string): PasteBlock => ({ id: `p${seq}`, seq, lines: content.split('\n').length, content })
const CODE = 'def a():\n    return 1\n'
const QUERY = 'SELECT 1\nFROM t\nWHERE x\n;'

describe('asSent', () => {
  it('puts each block in place of its marker and sends the blocks it holds', () => {
    const sent = asSent(`see ${markerFor(1)} and ${markerFor(2)} please`, [block(1, CODE), block(2, QUERY), block(3, 'unused\n\n\n')])
    expect(sent.text).toBe(`see ${CODE} and ${QUERY} please`)
    expect(sent.pastes.map((p) => [p.seq, p.content])).toEqual([[1, CODE], [2, QUERY]])
  })

  it('leaves a marker with no block as she typed it', () => {
    expect(asSent(`what is ${markerFor(4)}?`, [block(1, CODE)])).toEqual({ text: `what is ${markerFor(4)}?`, pastes: [] })
  })

  it('keeps a block at either end as the trimmed message holds it', () => {
    const sent = asSent(`${markerFor(1)} then ${markerFor(2)}`, [block(1, `\n  ${QUERY}`), block(2, CODE)])
    expect(sent.text).toBe(`${QUERY} then ${CODE.trimEnd()}`)
    expect(sent.pastes.map((p) => p.content)).toEqual([QUERY, CODE.trimEnd()])
    expect(sent.pastes.every((p) => sent.text.includes(p.content))).toBe(true)
    expect(sent.pastes[1].lines).toBe(2)
  })
})

describe('takeIn', () => {
  it('keeps a block its number when the composer holds no other under it', () => {
    expect(takeIn(`x ${markerFor(1)}`, [block(1, CODE)], [])).toEqual({ text: `x ${markerFor(1)}`, blocks: [block(1, CODE)].map((b) => ({ ...b, id: expect.any(String) })) })
  })

  it('renumbers a block whose number the composer holds for another, marker and all', () => {
    const back = takeIn(`fix ${markerFor(1)} then ${markerFor(2)}`, [block(1, CODE), block(2, QUERY)], [block(1, 'her draft paste\n\n\n\n')])
    expect(back.text).toBe(`fix ${markerFor(3)} then ${markerFor(2)}`)
    expect(back.blocks.map((b) => [b.seq, b.content])).toEqual([[3, CODE], [2, QUERY]])
  })

  it('adds no second copy of a block the composer already holds', () => {
    expect(takeIn(`again ${markerFor(1)}`, [block(1, CODE)], [block(1, CODE)])).toEqual({ text: `again ${markerFor(1)}`, blocks: [] })
  })
})
