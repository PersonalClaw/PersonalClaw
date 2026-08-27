/** Pure edits over a `DeckModelJson` — no React, no fetch, no DOM.
 *
 *  The deck-side counterpart of `sheetModelEdit.ts`, and separate from it for the same
 *  reason: the interesting rules (what indenting a bullet means, how a slide is replaced
 *  without disturbing its neighbours) are testable as data, and a component test that had
 *  to render a slide list to check them would be measuring React.
 *
 *  **Every function returns a NEW model.** The editor's dirty flag is identity-based
 *  (`model !== loaded.model`), so an in-place mutation would edit the deck and leave the
 *  Save button convinced there was nothing to save.
 */
import type { DeckBulletJson, DeckModelJson, DeckShapeBoxJson, DeckSlideJson } from '../../lib/api'

/** PowerPoint's own bullet range, mirrored from `MAX_BULLET_LEVEL` on the server. Held
 *  here as well because the control that offers a depth has to know what it may offer —
 *  and the server clamps rather than refusing, so a UI that let a user pick 12 would show
 *  them a choice that silently became 8. */
export const MAX_BULLET_LEVEL = 8

/** A blank shape box: the absence of an override, not a position at the top-left. */
export function inheritedBox(): DeckShapeBoxJson {
  return { left_in: 0, top_in: 0, width_in: 0, height_in: 0 }
}

/** Whether a box overrides its layout's position. Keyed on the SIZE, mirroring
 *  `ShapeBox.placed`: a shape flush against the corner still has zero left/top. */
export function isPlaced(box: DeckShapeBoxJson | null | undefined): boolean {
  return !!box && box.width_in > 0 && box.height_in > 0
}

/** A placed box in words, for the line that tells a user their shape was moved. */
export function boxSummary(box: DeckShapeBoxJson): string {
  const n = (v: number) => `${Math.round(v * 100) / 100}`
  return `${n(box.left_in)} × ${n(box.top_in)} in from the top-left, ${n(box.width_in)} × ${n(box.height_in)} in`
}

/** A blank slide, spelled once so every insertion path agrees what "new" is. */
export function emptySlide(): DeckSlideJson {
  return {
    title: '', bullets: [], notes: '', artifact_slug: '', layout: '',
    title_box: inheritedBox(), body_box: inheritedBox(),
  }
}

/** What the slide list shows for one slide — its title, or an honest stand-in.
 *
 *  A deck of slides all labelled "Slide 3" is unnavigable, and a bare index would hide
 *  that an untitled slide HAS content. So a titleless slide borrows its first bullet. */
export function slideLabel(slide: DeckSlideJson, index: number): string {
  const title = slide.title.trim()
  if (title) return title
  const first = slide.bullets.find((b) => b.text.trim())
  if (first) return first.text.trim()
  return `Slide ${index + 1}`
}

/** Replace one slide, leaving every other one untouched. */
export function withSlide(model: DeckModelJson, index: number, next: DeckSlideJson): DeckModelJson {
  return { ...model, slides: model.slides.map((s, i) => (i === index ? next : s)) }
}

/** Replace one bullet of one slide. */
export function withBullet(
  model: DeckModelJson,
  slideIndex: number,
  bulletIndex: number,
  next: DeckBulletJson,
): DeckModelJson {
  const slide = model.slides[slideIndex]
  if (!slide) return model
  return withSlide(model, slideIndex, {
    ...slide,
    bullets: slide.bullets.map((b, i) => (i === bulletIndex ? next : b)),
  })
}

/** Add a bullet at the end, at the depth of the one above it.
 *
 *  Inheriting the depth is the behaviour every outliner has: a person typing a list of
 *  sub-points should not have to re-indent each one, and the alternative (always top
 *  level) makes the depth this atom added expensive to use. */
export function withAppendedBullet(model: DeckModelJson, slideIndex: number): DeckModelJson {
  const slide = model.slides[slideIndex]
  if (!slide) return model
  const last = slide.bullets[slide.bullets.length - 1]
  return withSlide(model, slideIndex, {
    ...slide,
    bullets: [...slide.bullets, { text: '', level: last ? last.level : 0 }],
  })
}

/** Remove one bullet. */
export function withoutBullet(model: DeckModelJson, slideIndex: number, bulletIndex: number): DeckModelJson {
  const slide = model.slides[slideIndex]
  if (!slide) return model
  return withSlide(model, slideIndex, {
    ...slide,
    bullets: slide.bullets.filter((_b, i) => i !== bulletIndex),
  })
}

/** Add a slide after *index* (or at the end when it is out of range). */
export function withAppendedSlide(model: DeckModelJson, index: number): DeckModelJson {
  const at = index >= 0 && index < model.slides.length ? index + 1 : model.slides.length
  const slides = model.slides.slice()
  slides.splice(at, 0, emptySlide())
  return { ...model, slides }
}

/** Remove one slide. */
export function withoutSlide(model: DeckModelJson, index: number): DeckModelJson {
  return { ...model, slides: model.slides.filter((_s, i) => i !== index) }
}

/** Clear a shape's placement so it inherits the layout's position again.
 *
 *  The editor does not AUTHOR geometry — it is a structural editor, not a canvas — but a
 *  deck whose title was dragged somewhere odd in PowerPoint has to be recoverable, and
 *  without this the position would be preserved forever with no way to let go of it. */
export function withInheritedBoxes(model: DeckModelJson, index: number): DeckModelJson {
  const slide = model.slides[index]
  if (!slide) return model
  return withSlide(model, index, { ...slide, title_box: inheritedBox(), body_box: inheritedBox() })
}

/** The layouts the shipped template offers, mirroring `DECK_LAYOUTS` on the server.
 *
 *  Mirrored rather than fetched: the server REFUSES a layout it does not have (400 with
 *  the path), and the parser reports one it cannot re-create rather than inventing a name,
 *  so the only values this list can meet are its own members or `''`. `layoutOptions`
 *  carries anything else through anyway, so drift would surface as an extra option rather
 *  than as a control that silently dropped the loaded document's layout. */
export const DECK_LAYOUTS = [
  'Title Slide', 'Title and Content', 'Section Header', 'Two Content', 'Comparison',
  'Title Only', 'Blank', 'Content with Caption', 'Picture with Caption',
  'Title and Vertical Text', 'Vertical Title and Text',
] as const

/** Layout options, plus the loaded slide's OWN layout when it is not one of them. */
export function layoutOptions(current: string): { value: string; label: string }[] {
  const known = !current || (DECK_LAYOUTS as readonly string[]).includes(current)
  return [
    { value: '', label: 'From the slide’s content' },
    ...DECK_LAYOUTS.map((name) => ({ value: name, label: name })),
    ...(known ? [] : [{ value: current, label: `${current} (this deck’s own)` }]),
  ]
}

/** The depth options a bullet may be given, labelled the way a person counts. */
export function levelOptions(): { value: string; label: string }[] {
  return Array.from({ length: MAX_BULLET_LEVEL + 1 }, (_v, level) => ({
    value: String(level),
    label: level === 0 ? 'Top level' : `Level ${level + 1}`,
  }))
}

/** The slide-size presets the editor offers, plus the deck's OWN size when it is neither.
 *
 *  Mirrors `formatOptions` in `sheetModelEdit.ts` and for the same reason: a 16:10 deck
 *  that opened showing "Widescreen" would have its size silently changed by a save the
 *  user did not think was about geometry. `0` is "the template's own size". */
export const SLIDE_SIZES: { key: string; label: string; width: number; height: number }[] = [
  { key: '', label: 'From the template', width: 0, height: 0 },
  { key: '16:9', label: 'Widescreen 16:9 (13.33 × 7.5 in)', width: 13.333, height: 7.5 },
  { key: '4:3', label: 'Standard 4:3 (10 × 7.5 in)', width: 10, height: 7.5 },
]

/** Which preset a deck's size IS, or `'custom'` when it is its own. */
export function slideSizeKey(model: DeckModelJson): string {
  if (!model.width_in && !model.height_in) return ''
  const match = SLIDE_SIZES.find(
    (s) => s.width > 0 && Math.abs(s.width - model.width_in) < 0.01 && Math.abs(s.height - model.height_in) < 0.01,
  )
  return match ? match.key : 'custom'
}

export function slideSizeOptions(model: DeckModelJson): { value: string; label: string }[] {
  const key = slideSizeKey(model)
  const own = key === 'custom'
    ? [{ value: 'custom', label: `This deck’s own size (${Math.round(model.width_in * 100) / 100} × ${Math.round(model.height_in * 100) / 100} in)` }]
    : []
  return [...SLIDE_SIZES.map((s) => ({ value: s.key, label: s.label })), ...own]
}

/** Apply a preset. `'custom'` is not applicable — it exists to REPORT a size, not to set
 *  one — so it is returned unchanged rather than resolving to a near neighbour. */
export function withSlideSize(model: DeckModelJson, key: string): DeckModelJson {
  const preset = SLIDE_SIZES.find((s) => s.key === key)
  if (!preset) return model
  return { ...model, width_in: preset.width, height_in: preset.height }
}
