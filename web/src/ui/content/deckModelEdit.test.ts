/** DFE-8 — the deck editor's rules, as data.
 *
 *  These are the decisions a component test could only reach through the DOM: what
 *  indenting means, what a new bullet inherits, what a slide is labelled when it has no
 *  title, and which slide size a deck IS. Asserted here so the component suite can be
 *  about the surface rather than about arithmetic.
 */
import { describe, expect, it } from 'vitest'
import type { DeckModelJson, DeckSlideJson } from '../../lib/api'
import {
  MAX_BULLET_LEVEL,
  boxSummary,
  emptySlide,
  inheritedBox,
  isPlaced,
  layoutOptions,
  levelOptions,
  slideLabel,
  slideSizeKey,
  slideSizeOptions,
  withAppendedBullet,
  withAppendedSlide,
  withBullet,
  withInheritedBoxes,
  withSlide,
  withSlideSize,
  withoutBullet,
  withoutSlide,
} from './deckModelEdit'

const slide = (over: Partial<DeckSlideJson> = {}): DeckSlideJson => ({ ...emptySlide(), ...over })

const MODEL: DeckModelJson = {
  title: 'Cover',
  width_in: 0,
  height_in: 0,
  slides: [
    slide({ title: 'One', bullets: [{ text: 'a', level: 0 }, { text: 'b', level: 2 }] }),
    slide({ title: 'Two' }),
  ],
}

describe('bullet depth', () => {
  it('offers exactly the depths PowerPoint can express, counted the way a person counts', () => {
    const options = levelOptions()
    expect(options).toHaveLength(MAX_BULLET_LEVEL + 1)
    expect(options[0]).toEqual({ value: '0', label: 'Top level' })
    expect(options[1].label).toBe('Level 2')
    expect(options[MAX_BULLET_LEVEL].value).toBe(String(MAX_BULLET_LEVEL))
  })

  it('changes one bullet and leaves its neighbours and the other slide alone', () => {
    const next = withBullet(MODEL, 0, 1, { text: 'b', level: 4 })
    expect(next.slides[0].bullets).toEqual([{ text: 'a', level: 0 }, { text: 'b', level: 4 }])
    // Identity, not just equality: the untouched slide must be the SAME object, or the
    // editor's identity-based dirty flag would report a change nobody made.
    expect(next.slides[1]).toBe(MODEL.slides[1])
    expect(next).not.toBe(MODEL)
    expect(MODEL.slides[0].bullets[1].level).toBe(2)
  })

  it('gives a new bullet the depth of the one above it', () => {
    // The behaviour every outliner has: typing a run of sub-points must not mean
    // re-indenting each one, or the depth this atom added is expensive to use.
    expect(withAppendedBullet(MODEL, 0).slides[0].bullets[2]).toEqual({ text: '', level: 2 })
    // …and the first bullet of an empty slide starts at the top level, since there is
    // nothing to inherit from.
    expect(withAppendedBullet(MODEL, 1).slides[1].bullets).toEqual([{ text: '', level: 0 }])
  })

  it('removes one bullet by position, not by matching its text', () => {
    const doubled = withSlide(MODEL, 0, slide({ bullets: [{ text: 'x', level: 0 }, { text: 'x', level: 1 }] }))
    expect(withoutBullet(doubled, 0, 0).slides[0].bullets).toEqual([{ text: 'x', level: 1 }])
  })
})

describe('the slide list', () => {
  it('labels a slide by its title, then its first bullet, then its number', () => {
    expect(slideLabel(slide({ title: 'Pipeline' }), 0)).toBe('Pipeline')
    // A titleless slide with content must not read as empty — borrowing the first bullet
    // is what stops a deck of "Slide 2, Slide 3" being unnavigable.
    expect(slideLabel(slide({ bullets: [{ text: 'no title here', level: 0 }] }), 1)).toBe('no title here')
    expect(slideLabel(slide(), 2)).toBe('Slide 3')
    // Whitespace is not a title.
    expect(slideLabel(slide({ title: '   ' }), 3)).toBe('Slide 4')
  })

  it('inserts a slide AFTER the current one and appends when the index is out of range', () => {
    expect(withAppendedSlide(MODEL, 0).slides.map((s) => s.title)).toEqual(['One', '', 'Two'])
    expect(withAppendedSlide(MODEL, 99).slides.map((s) => s.title)).toEqual(['One', 'Two', ''])
  })

  it('removes one slide and keeps the deck title', () => {
    const next = withoutSlide(MODEL, 0)
    expect(next.slides.map((s) => s.title)).toEqual(['Two'])
    expect(next.title).toBe('Cover')
  })
})

describe('geometry', () => {
  it('reads placement from the SIZE, so a shape at the corner still counts as placed', () => {
    expect(isPlaced(inheritedBox())).toBe(false)
    expect(isPlaced({ left_in: 0, top_in: 0, width_in: 4, height_in: 1 })).toBe(true)
    // A box with a position but no size is not a placement — it is the absence of one.
    expect(isPlaced({ left_in: 2, top_in: 2, width_in: 0, height_in: 0 })).toBe(false)
  })

  it('releases both boxes back to the layout', () => {
    const placed = withSlide(MODEL, 0, slide({
      title_box: { left_in: 1, top_in: 1, width_in: 3, height_in: 1 },
      body_box: { left_in: 1, top_in: 3, width_in: 3, height_in: 2 },
    }))
    const released = withInheritedBoxes(placed, 0)
    expect(isPlaced(released.slides[0].title_box)).toBe(false)
    expect(isPlaced(released.slides[0].body_box)).toBe(false)
  })

  it('says where a placed shape is, rounded to something a person reads', () => {
    expect(boxSummary({ left_in: 1.2499, top_in: 0.5, width_in: 6, height_in: 1.5 }))
      .toBe('1.25 × 0.5 in from the top-left, 6 × 1.5 in')
  })
})

describe('layout and slide size', () => {
  it('offers the template layouts plus "from the content"', () => {
    const options = layoutOptions('')
    expect(options[0]).toEqual({ value: '', label: 'From the slide’s content' })
    expect(options.map((o) => o.value)).toContain('Title and Content')
  })

  it('carries a layout it does not know rather than dropping it', () => {
    // The server refuses an unknown layout, so this is defence against DRIFT between the
    // mirrored list and `DECK_LAYOUTS` — and dropping the loaded value silently would
    // change the document by opening it.
    const options = layoutOptions('Acme Section Break')
    expect(options.map((o) => o.value)).toContain('Acme Section Break')
  })

  it('names the deck’s size when it is a preset and reports its own when it is not', () => {
    expect(slideSizeKey({ ...MODEL, width_in: 0, height_in: 0 })).toBe('')
    expect(slideSizeKey({ ...MODEL, width_in: 13.333, height_in: 7.5 })).toBe('16:9')
    expect(slideSizeKey({ ...MODEL, width_in: 10, height_in: 7.5 })).toBe('4:3')
    expect(slideSizeKey({ ...MODEL, width_in: 12, height_in: 7.5 })).toBe('custom')
    // A deck with its own size must SEE its own size in the control, or a save the user
    // thought was about a bullet would silently resize the deck.
    expect(slideSizeOptions({ ...MODEL, width_in: 12, height_in: 7.5 }).map((o) => o.value))
      .toContain('custom')
    expect(slideSizeOptions({ ...MODEL, width_in: 10, height_in: 7.5 }).map((o) => o.value))
      .not.toContain('custom')
  })

  it('applies a preset and refuses to resolve "custom" to a near neighbour', () => {
    expect(withSlideSize(MODEL, '16:9')).toMatchObject({ width_in: 13.333, height_in: 7.5 })
    expect(withSlideSize(MODEL, '')).toMatchObject({ width_in: 0, height_in: 0 })
    const own = { ...MODEL, width_in: 12, height_in: 7.5 }
    expect(withSlideSize(own, 'custom')).toBe(own)
  })
})
