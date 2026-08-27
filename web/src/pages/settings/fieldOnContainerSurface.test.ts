import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A text field must not paint exactly its own backdrop ────────────────────────────────────────
//
// `TextInput`'s at-rest chrome is its FILL alone: `INPUT_BASE` sets no border and no shadow (only a
// `focus:ring`), so the single thing separating a field from the panel behind it is
// `FIELD_SURFACE[surface]`. The default is `container` — and several settings panels wrap their rows
// in `bg-surface-container`, the very same token. A default field inside one of those wrappers paints
// its backdrop's colour exactly and has no edge whatsoever.
//
// Measured on a real build, sampling every visible text field on all 31 settings panels and comparing
// each field's computed fill against the first ancestor that actually paints (canvas-normalised, so
// `oklab()` backdrops resolve too):
//
//   dark   48 fields · 47 with no border and no shadow · 3 at exactly 1.00:1
//   light  48 fields · 47 with no border and no shadow · 3 at exactly 1.00:1
//
//   the three, identical in both themes — all fixed here:
//     #/settings/companion  "e.g. Living room Mac"                 rgb(30,31,32) on rgb(30,31,32)
//     #/settings/sources    "~/notes/today.md"
//     #/settings/packs      "https://example.com/connector_..."
//
// `surface="high"` is the form the rest of the app already uses for a field on a container backdrop
// (ArchivePanel, AuditPanel, MemoryPanel, ModelBackends, MultiInstanceCard), so this converges the
// outliers onto the existing canonical shape rather than inventing one.
//
// ⚠️ WHAT THIS DOES NOT CLAIM. `surface="high"` takes these fields from invisible to *perceptible*
// and consistent with their siblings — it does NOT reach WCAG 1.4.11's 3:1 for component boundaries.
// The same sweep found 47 of 47 borderless fields below 3:1 in dark (42 of them between 1.1 and
// 1.5:1), because no field in the app has an at-rest boundary. Closing that needs a border on
// `INPUT_BASE`, i.e. a change to every field in the product — an owner decision, recorded in the
// cycle ledger, deliberately not made here.
//
// ⚠️ WHY THIS RAIL PINS SITES INSTEAD OF MATCHING THE TREE. Deciding this correctly needs the RENDERED
// backdrop: whether a field's ancestor paints `bg-surface-container` depends on JSX nesting a regex
// cannot see, and 44 fields with the same default are perfectly fine because their backdrop differs.
// The honest detector is the DOM sweep above, which cannot run in jsdom. So this pins the sites the
// sweep judged, and the sweep is the thing to re-run when a panel grows a field.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

/** All three measured wrappers are now `settingsUI`'s shared `RowGroup`, so the vacuity guards below
 *  have to FOLLOW that indirection instead of grepping the panel for the raw class.
 *
 *  🪤 Two of the three guards did not go red when the class moved — they kept passing by matching an
 *  UNRELATED `bg-surface-container` block elsewhere in the same file (`CompanionPanel:111`,
 *  `PacksPanel:147+`), i.e. they became false passes about a wrapper the field is not in. Only
 *  SourcesPanel, which had no other container block left, actually failed. So this checks both links
 *  in the chain: the panel wraps its rows in `RowGroup`, and `RowGroup` still paints the container
 *  tone that makes a default `surface="container"` field invisible. */
const wrapsRowsOnAContainerSurface = (panel: string) => {
  expect(read(`pages/settings/${panel}.tsx`), `${panel} must wrap its rows in RowGroup`)
    .toMatch(/<RowGroup[\s>]/)
  const rowGroup = read('pages/settings/settingsUI.tsx').match(/export function RowGroup\([\s\S]*?\n\}/)?.[0] ?? ''
  expect(rowGroup, 'RowGroup must exist to be the wrapper').toContain('Surface')
  expect(rowGroup, 'and it must still paint bg-surface-container — the reason surface="high" is needed')
    .toMatch(/tone="container"/)
}

describe('a settings field on a container backdrop lifts its surface', () => {
  it('SourcesPanel scratchpad field passes surface="high"', () => {
    const src = read('pages/settings/SourcesPanel.tsx')
    const tag = src.match(/<TextInput[\s\S]{0,260}?placeholder="~\/notes\/today\.md"/)?.[0] ?? ''
    expect(tag, 'the scratchpad field must exist').toContain('<TextInput')
    expect(tag, 'it sits in a bg-surface-container row, so it must lift off it').toContain('surface="high"')
  })

  it('the SourcesPanel row really is a container-surfaced wrapper', () => {
    // Vacuity guard: if the wrapper ever stops painting bg-surface-container the fix above becomes
    // unnecessary, and this rail should be re-derived rather than left asserting a stale reason.
    wrapsRowsOnAContainerSurface('SourcesPanel')
  })

  it('PacksPanel TextRow passes surface="high"', () => {
    const src = read('pages/settings/PacksPanel.tsx')
    const tag = src.match(/<TextInput[\s\S]{0,300}?onKeyDown/)?.[0] ?? ''
    expect(tag, 'the shared TextRow field must exist').toContain('<TextInput')
    expect(tag).toContain('surface="high"')
  })

  it('PacksPanel TextRow callers sit on a container surface', () => {
    wrapsRowsOnAContainerSurface('PacksPanel')
  })

  it('CompanionPanel instance-name field passes surface="high"', () => {
    // The third measured site. It arrived in CA-4 after this stack was cut, so it was fixed once the
    // stack was reparented onto a main that carried it — the family is complete rather than 2 of 3.
    const src = read('pages/settings/CompanionPanel.tsx')
    const tag = src.match(/<TextInput[\s\S]{0,200}?placeholder="e\.g\. Living room Mac"/)?.[0] ?? ''
    expect(tag, 'the instance-name field must exist').toContain('<TextInput')
    expect(tag).toContain('surface="high"')
  })

  it('the CompanionPanel row really is a container-surfaced wrapper', () => {
    wrapsRowsOnAContainerSurface('CompanionPanel')
  })

  it('TextInput still defaults to the container surface, and still has no at-rest border', () => {
    // The whole defect depends on both facts. If either changes, these fixes and this rail's
    // reasoning must be revisited — so assert them rather than assume them.
    const forms = read('ui/forms.tsx')
    expect(forms, 'default surface').toMatch(/surface = 'container'/)
    expect(forms, 'container maps to the same token the wrappers use').toMatch(/container: 'bg-surface-container'/)
    expect(forms, 'high is a distinct step').toMatch(/high: 'bg-surface-high'/)
    const base = forms.match(/const INPUT_BASE = '[^']*'/)?.[0] ?? ''
    expect(base, 'INPUT_BASE must exist').toContain('INPUT_BASE')
    expect(/\bborder\b|ring-1/.test(base.replace(/focus:[^\s']*/g, '')),
      'no at-rest border/ring — the fill IS the affordance, which is why surface matters').toBe(false)
  })

  it('the pre-fix shape does not come back at either site', () => {
    const sources = read('pages/settings/SourcesPanel.tsx')
    const packs = read('pages/settings/PacksPanel.tsx')
    // A bare field (no surface prop) directly before these placeholders is the defect shape.
    expect(/<TextInput value=\{scratchpad\} onChange=\{setScratchpad\} mono placeholder/.test(sources)).toBe(false)
    expect(/<TextInput value=\{draft\} onChange=\{setDraft\} placeholder=\{placeholder\} ariaLabel=\{label\} mono\n/.test(packs)).toBe(false)
  })
})
