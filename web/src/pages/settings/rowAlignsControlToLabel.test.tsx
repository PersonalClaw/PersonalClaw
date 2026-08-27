import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { Row } from './settingsUI'
import { Toggle } from '../../ui/Toggle'

// ── A settings Row centres its control on its LABEL, not on the label+hint block ────────────────
//
// `Row` was a two-column flex with `items-center`, which centres the right-hand control against the
// WHOLE left block. With a one-line hint that is invisible; with a hint that wraps, the control drifts
// down by half the wrapped height and detaches from the label it belongs to. Measured live on a
// `demo-home` gateway across all 34 `#/settings/*` routes — control centre-y minus label centre-y over
// the 103 rendered rows in 18 panels, before → after:
//
//     390x844    100 of 103 off by >1px, median 30.25px, worst 225.25px   →  0.00px on all 103
//     834x1112   100 of 103,             median 20.50px, worst  79.00px   →  0.00px on all 103
//     1280x900   100 of 103,             median 10.75px, worst  40.00px   →  0.00px on all 103
//     1440x1000  100 of 103,             median 10.75px, worst  40.00px   →  0.00px on all 103
//
// 93 of the 103 wrap their hint at 390px, so this was the normal case on a phone. It was already
// shaping product copy too: the Evaluations panel trimmed its own hint from 456 to 148 characters to
// keep its switch near its label rather than change the primitive.
//
// 🪤 WHY THIS RAIL IS NOT A PIXEL ASSERTION. jsdom implements no layout — every
// `getBoundingClientRect()` is 0x0 at 0,0 — so "the deltas are equal" is vacuously true here and
// would pass just as well on the broken flex row. The rail instead pins the MECHANISM that forces
// the delta to zero for every control height, hint length, viewport and density: the label and the
// control occupy the SAME grid row, and the hint is pushed to a second row. That is checkable
// without layout, and it is the thing a future edit would break.

const SRC = join(process.cwd(), 'src')
const SETTINGS_UI = 'pages/settings/settingsUI.tsx'

/** Every non-test `.tsx` under `web/src`, walked in process.
 *
 *  🪤 NOT `git grep`. The first draft of this rail shelled out to `git grep` three times and each
 *  call intermittently blew the 20s test timeout under the full suite — a rail that fails for
 *  reasons unrelated to what it guards teaches people to re-run it, which is how a real red gets
 *  waved through. An in-process walk is the pattern the sibling rails already use
 *  (`ui/listRowNaming.test.tsx`), it needs no repository, and it cannot contend for a git lock. */
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const sources = (): { file: string; src: string }[] =>
  walk(SRC).map((p) => ({ file: relative(SRC, p), src: readFileSync(p, 'utf8') }))

/** The files that import from `settingsUI`, i.e. the real consumer set. */
const consumerFiles = (all: { file: string; src: string }[]) =>
  all.filter(({ src }) => /from '(\.|\.\.)+\/(pages\/settings\/)?settingsUI'/.test(src))

/** The opening tags of `<Row …>` in a source file.
 *
 *  🪤 A JSX OPENING TAG DOES NOT END AT THE FIRST `>`. An attribute value can hold an arrow function
 *  (`onChange={(v) => …}`) or nested JSX (`right={<Chip>x</Chip>}`), each of which contains `>`
 *  characters that are not the tag's end. Skipping `=>` is not enough either — the nested-JSX case
 *  has a bare `>`. So track brace depth and quote state, and only accept a `>` at depth 0 outside a
 *  string. Returns the attribute text, which is what the hint census reads. */
function openingTags(src: string, name = 'Row'): string[] {
  const tags: string[] = []
  const re = new RegExp(`<${name}(?=[\\s/>])`, 'g')
  let m: RegExpExecArray | null
  while ((m = re.exec(src))) {
    let i = m.index + name.length + 1
    let depth = 0
    let quote: string | null = null
    for (; i < src.length; i++) {
      const c = src[i]
      if (quote) { if (c === quote) quote = null; continue }
      if (c === '"' || c === "'" || c === '`') { quote = c; continue }
      if (c === '{') { depth++; continue }
      if (c === '}') { depth--; continue }
      if (c === '>' && depth === 0) break
    }
    tags.push(src.slice(m.index, i + 1))
  }
  return tags
}

describe('the settings Row population this invariant protects', () => {
  it('is a real, load-bearing population — not a vacuous scan', () => {
    const consumers = consumerFiles(sources())
    const direct = consumers.flatMap(({ file, src }) => openingTags(src).map((t) => ({ file, t })))
    // `ToggleRow` renders a `Row` internally, so its call sites are Rows on screen too — and five
    // panels reach `Row` ONLY through it (Ambient, Sources, Legibility, Models, Apps). Counting just
    // `<Row>` would under-report the blast radius by those five whole panels.
    const viaToggle = consumers.flatMap(({ file, src }) => openingTags(src, 'ToggleRow').map((t) => ({ file, t })))
    const all = [...direct, ...viaToggle]
    const hinted = all.filter(({ t }) => /\bhint=/.test(t))
    const panels = new Set(all.map(({ file }) => file))
    // Floors, not equalities: this is a growing surface, and a rail that has to be edited every time
    // a panel gains a row gets edited without being read. The counts when this landed: 41 files
    // import settingsUI, 74 `<Row>` call sites in 18 of them, plus 22 `<ToggleRow>` sites, for 96
    // rows across 22 panels — and 95 of the 96 pass a hint. The floors sit below those so they fire
    // on a COLLAPSE (the scanner matching nothing, or `Row` abandoned for a fourth hand-roll) rather
    // than on ordinary growth.
    expect(consumers.length, 'files importing from settingsUI').toBeGreaterThan(30)
    expect(direct.length, 'direct <Row> call sites — if this collapses, the tag scanner broke').toBeGreaterThan(60)
    expect(viaToggle.length, '<ToggleRow> call sites, each a Row on screen').toBeGreaterThan(15)
    expect(panels.size, 'panels rendering a settings row').toBeGreaterThan(18)
    // 95 of 96 is what makes this a primitive-level defect rather than an edge case: the hint is not
    // an occasional extra, it is what a settings row IS. Floor the ratio too, so a future tree where
    // most rows are hintless forces this rail to be re-derived instead of quietly continuing to
    // claim the defect was universal.
    expect(hinted.length, 'rows passing a hint: the rows whose control could drift').toBeGreaterThan(80)
    expect(hinted.length / all.length, 'a hinted row is the normal row, not the outlier').toBeGreaterThan(0.85)
  })

  it('the tag scanner survives a `>` inside an attribute', () => {
    // Vacuity guard for the scanner itself: if it stopped at the first `>` it would return a
    // truncated tag and silently miss the `hint` of any row with an arrow function or nested JSX
    // before it, under-counting the population it is supposed to floor.
    const tags = openingTags('<Row onChange={(v) => f(v)} right={<Chip a={1}>hi</Chip>} hint="h" label="l">x</Row>')
    expect(tags).toHaveLength(1)
    expect(tags[0], 'the whole tag, including the hint that follows a nested `>`').toContain('hint="h"')
    expect(tags[0]).toContain('label="l"')
    // `<Row` must not also match `<RowGroup` — the same prefix collision that already broke
    // `oneControlPerRow`'s source anchor one level up.
    expect(openingTags('<RowGroup><Row label="a">x</Row></RowGroup>'), 'RowGroup is not a Row').toHaveLength(1)
    expect(openingTags('<ToggleRow label="a" />', 'ToggleRow')).toHaveLength(1)
  })
})

describe('a settings Row puts its control on the label\'s row', () => {
  const renderRow = (hint?: string) => {
    const { container } = render(<Row label="Encrypt shards" hint={hint}><Toggle on={false} onChange={() => {}} label="Encrypt shards" /></Row>)
    const row = container.querySelector('div.grid') as HTMLElement
    expect(row, 'Row must render a grid container').not.toBeNull()
    return { row, kids: [...row.children] as HTMLElement[] }
  }

  it('is a two-column grid whose first column can shrink', () => {
    const { row } = renderRow('a hint')
    const cls = row.className
    // `minmax(0,1fr)` is what carries the old left wrapper's `min-w-0`: without it the label column
    // takes its min-content width and a long hint pushes the control off the row instead of wrapping.
    expect(cls, 'two columns: shrinkable label, auto control').toContain('grid-cols-[minmax(0,1fr)_auto]')
    expect(cls, 'each grid ROW centres its own items').toContain('items-center')
    expect(cls, 'a flex row is the shape this replaced').not.toMatch(/(^|\s)flex(\s|$)/)
    expect(cls, 'justify-between belongs to the flex shape').not.toContain('justify-between')
    // `items-start` is the cheap alternative that leaves the control (ctlH - lineH)/2 low. If a
    // future edit reaches for it, this rail should say so rather than let the drift come back
    // smaller.
    expect(cls, 'items-start is not the fix — it top-aligns instead of centring on the label').not.toContain('items-start')
  })

  it('places the control in column 2 of the LABEL\'s row, and the hint below it', () => {
    const { kids } = renderRow('a hint that would wrap on a phone')
    expect(kids, 'label, hint, control — DOM order unchanged').toHaveLength(3)
    const [label, hintEl, slot] = kids
    expect(label.textContent).toBe('Encrypt shards')
    // The hint keeps the id `Row` publishes through `FieldHintProvider`. Whether the CONTROL then
    // claims it is that control's business and a different rail's: measured here, `Toggle` never
    // reads `useFieldHintId()`, so on a switch row the id is published and unconsumed. That is a
    // real gap and it is NOT this change's — it predates the grid and is unaffected by it.
    expect(hintEl.id, 'the hint keeps its id, so a control that claims it still can').toBeTruthy()
    // The whole invariant, in the only terms jsdom can see: the control is pinned to row 1, which is
    // also where the auto-placed label lands. Explicitly-placed items are positioned BEFORE
    // auto-placed ones, so pinning the control to r1c2 is what pushes the hint to row 2 — drop
    // either half and the hint and the control collide or swap rows.
    expect(slot.className, 'control pinned to row 1, column 2').toContain('col-start-2 row-start-1')
    expect(label.className, 'the label must AUTO-place, so it shares row 1 with the control').not.toMatch(/row-start-/)
    expect(hintEl.className, 'the hint must AUTO-place, so it falls to row 2').not.toMatch(/row-start-|col-start-/)
    // A block slot builds a line box around an inline-level control and leaves the strut's descender
    // space below it, so the control sits low of centre even in a correctly centred track.
    expect(slot.className, 'the slot height must be the control height, not a line box').toContain('flex items-center')
    expect(slot.children, 'all children share ONE slot — see oneControlPerRow').toHaveLength(1)
  })

  it('still works with no hint at all', () => {
    const { kids } = renderRow(undefined)
    expect(kids, 'label, control').toHaveLength(2)
    expect(kids[1].className).toContain('col-start-2 row-start-1')
  })
})

describe('the fix is not half-applied', () => {
  it('no NEW hand-rolled copy of the old flex row appears in the tree', () => {
    // The old container class string is still spelled out at three RECORD-row sites — DevicesPanel's
    // device list, GuardrailsPanel's autonomy ladder and its HealthRow. Those are deliberately not
    // converted: each holds an icon plus two to four sublines rather than one label and one hint, so
    // "centre the control on the label" is not even a well-formed request there, and their alignment
    // is a separate list-row question.
    //
    // This is a RATCHET, not an allowlist to grow: it pins the count so a fourth copy — which is how
    // the flex shape would come back after being removed from the primitive — has to be looked at
    // rather than merged quietly. Adding a real label+hint+control row means using `Row`.
    const OLD = /flex items-(start|center) justify-between gap-l border-b border-outline-variant\/30 py-[23] last:border-0/g
    const hits = sources().flatMap(({ file, src }) => (src.match(OLD) ?? []).map(() => file))
    // Vacuity floor: a pattern that matches nothing reads exactly like a clean tree. The scanned
    // population has to be non-empty and big enough to contain the tree, or the `toBe(3)` below is
    // meaningless.
    expect(sources().length, 'the walk must find the tree').toBeGreaterThan(300)
    expect(hits.length, 'a pattern matching nothing looks identical to a fixed tree').toBeGreaterThan(0)
    expect(hits.sort(), 'record rows carrying the old container string').toEqual([
      'pages/settings/DevicesPanel.tsx',
      'pages/settings/GuardrailsPanel.tsx',
      'pages/settings/GuardrailsPanel.tsx',
    ])
    expect(hits, 'the primitive itself must not hold the old shape').not.toContain(SETTINGS_UI)
  })

  it('Row is the only label-left/control-right settings row primitive', () => {
    // The brief for this change asked whether there are TWO implementations — a `ui/Row` and a
    // settings-local one. There is not: `ui/` has `FilterRow`, `MoreRow` and `RowHitTarget`, which
    // are list-row concerns, and `ui/forms`' `Field` is the STACKED shape (label above control).
    // Pinned so a second one cannot appear without this rail objecting.
    const decls = sources().filter(({ src }) => /^export function Row\(/m.test(src)).map(({ file }) => file)
    expect(decls).toEqual([SETTINGS_UI])
  })
})
