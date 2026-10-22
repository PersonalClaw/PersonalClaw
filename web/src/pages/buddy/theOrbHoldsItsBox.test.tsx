import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { viewOf } from '../../lib/buddy/machine'
import { BuddyStage } from './BuddyStage'
import { CONTROLS, advance, fire, startPlayground, use, type Playground } from './playground'

// ── The buddy is one orb of one size that stays where it is put (#5216) ──────────────────────────
//
// Driven through every control of the playground's matrix, three rounds, with the clock run past
// the timers each step starts, the orb is drawn the same way in every state: one slot, the same
// node while it shows, its drawing at 48 and the slot's own classes and style the same whatever
// the machine says. What the buddy has to say is a card beside it, in an area laid out apart from
// the slot (absolutely placed, never around it), so opening, growing, shrinking or closing it takes
// no room from the orb. The box itself, read in a browser, is `e2e/buddyPlayground.spec.ts`'s.

afterEach(cleanup)

/** Every step of the sweep: each control in turn, the timers run between rounds; docked, then
 *  floating, then docked again. */
function* sweep(): Generator<{ pg: Playground; step: string }> {
  let pg = startPlayground()
  yield { pg, step: 'start' }
  for (let round = 0; round < 3; round++) {
    // The second round plays floating: a drag dropped away from the top edge.
    if (round === 1) {
      pg = use(use(use(pg, 'start'), 'drag'), 'dropFloating')
      yield { pg, step: `${round}:floating` }
    }
    for (const control of CONTROLS) {
      if (round === 1 && control.id === 'dropDocked') continue
      pg = use(pg, control.id)
      yield { pg, step: `${round}:${control.id}` }
      pg = fire(pg)
      yield { pg, step: `${round}:${control.id}+timer` }
    }
    pg = advance(pg, 15 * 60_000)
    yield { pg, step: `${round}:later` }
  }
}

const orbSlot = (root: ParentNode) => root.querySelectorAll<HTMLElement>('[data-buddy-orb-slot]')

describe('the orb holds its box', () => {
  it('in every state and event of the matrix: one slot, one node while it shows, drawn at 48, the same classes', () => {
    const view = render(<BuddyStage machine={startPlayground().machine} width="full" steps={{}} onAnswer={() => {}} />)
    let shown: HTMLElement | null = null
    let look: string | null = null
    const rooms = new Set<string>()
    let cards = 0
    for (const { pg, step } of sweep()) {
      view.rerender(<BuddyStage machine={pg.machine} width="full" steps={pg.steps} onAnswer={() => {}} />)
      const v = viewOf(pg.machine)
      const slots = orbSlot(view.container)
      if (!v.shows) {
        expect(slots.length, `${step}: nothing shows`).toBe(0)
        shown = null
        continue
      }
      rooms.add(v.room ?? 'none')
      expect(slots.length, `${step}: one orb`).toBe(1)
      const slot = slots[0]
      if (shown) expect(slot, `${step}: the same orb, never drawn again in another place`).toBe(shown)
      shown = slot
      const sized = slot.querySelector<HTMLElement>('[data-buddy-orb] > span')
      expect(sized?.style.width, `${step}: drawn at 48`).toBe('48px')
      expect(sized?.style.height).toBe('48px')
      const here = `${slot.className}|${slot.getAttribute('style') ?? ''}`
      if (look) expect(here, `${step}: the slot's look does not follow the state`).toBe(look)
      look = here
      for (const area of view.container.querySelectorAll<HTMLElement>('[data-buddy-card-area]')) {
        cards++
        expect(area.contains(slot), `${step}: the card is beside the orb, never around it`).toBe(false)
        expect(area.className.split(/\s+/), `${step}: the card is laid out apart from the orb`).toContain('absolute')
        expect(area.querySelectorAll('[data-buddy-card-surface]').length, `${step}: one card in its area`).toBe(1)
      }
    }
    // The sweep reached the rooms the old shape drew at other sizes, and opened cards.
    for (const room of ['peek', 'compact', 'expanded', 'floating']) expect([...rooms], room).toContain(room)
    expect(cards).toBeGreaterThan(0)
  })
})
