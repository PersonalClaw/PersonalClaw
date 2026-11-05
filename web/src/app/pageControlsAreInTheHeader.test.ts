// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { join, relative } from 'node:path'
import { filesUnder, readSource } from '../test/sourceTree'

// ── Every page-level control is in the page header's action area ─────────────────────────────
//
// Some pages kept their view switch, filters, search and New in the header, and others put them in
// a bar under it (`WorkbenchLayout`'s `controls` slot, filled with `ListControls`), beside the
// title, or at the top of the body: the same kind of control in a different place on each page.
// The rule: every page-level control lives in the header's action area (`TopBar.right`, a
// `HeaderActions` cluster), right-aligned, in one order, search, filter and sort, the view switch,
// secondary actions, the primary action last:
//
//  · the search that filters the page is a `HeaderSearch`;
//  · the page's main view switch is its one `HeaderSegmented`;
//  · every other choice set, scope, status, kind, owner, sort, is a section of its `HeaderFilter`,
//    whose label says what is set;
//  · a control that belongs to one section of a page (a pane, a dialog, a form's field) stays with
//    that section: those are named below with why;
//  · a link in the title (a breadcrumb, such as a project's name before "Tasks", back to its
//    Overview) says where you are, in `TopBar.left`: navigation, not a page control, so not counted.
//
// So no page draws `ListControls`, a `FilterMenu` or a `FilterChip`, and a page that owns a header
// draws no `Segmented`, `SearchField` or row of pressed toggles (a `role="group"` of `aria-pressed`
// buttons, a filter by another name) of its own beyond its sections'. Measured against a budget that
// holds what had not moved yet and ratchets to nothing.

const SRC = join(process.cwd(), 'src')
const PAGES = join(SRC, 'pages')
// Line comments first: a `//` line that mentions `/*` would otherwise open a block that swallows
// code down to the next `*/`, and the count would miss what it held.
const strip = (t: string) => t.replace(/^\s*\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '')
const pageFiles = () => filesUnder(PAGES, (n) => /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n))
const rel = (abs: string) => relative(SRC, abs)
const count = (src: string, re: RegExp) => [...src.matchAll(re)].length

/** A section's own controls in a page that owns a header: not page-level, so not counted. */
const SECTION_CONTROLS: Record<string, { segmented?: number; search?: number; toggles?: number; why: string }> = {
  'pages/files/FilesSection.tsx': { search: 1, why: "the explorer pane's search of the file tree" },
  'pages/tools/ToolsPage.tsx': { segmented: 3, why: "the add and edit server dialogs' type and transport fields" },
  'pages/loop/LoopComposer.tsx': { segmented: 3, why: "the composer's own fields: granularity, project kind, loop kind" },
  'pages/loops/DesignCockpitPage.tsx': { segmented: 1, why: "the design preview pane's colour scheme" },
  'pages/loops/LoopPlanReview.tsx': { segmented: 1, toggles: 1, why: "plan fields: the goal type, and each phase's capabilities" },
  'pages/knowledge/SourceCreatePage.tsx': { segmented: 1, why: 'a form field, the enrichment for this source' },
  'pages/prompts/PromptCreatePage.tsx': { segmented: 2, why: "a form field (the prompt kind) and the editor's right-rail pane" },
  'pages/triggers/TriggerCreatePage.tsx': { segmented: 1, why: 'a form field, the trigger kind' },
  'pages/learning/LearningPage.tsx': { segmented: 1, why: "the Proposals section's own kind filter, under its heading" },
}

/** The page-level controls not yet in the header, by file: a page drawing one outside its header
 *  is held to this, so a row comes in only with a page that moves later. Empty, since every page's
 *  controls moved. */
const BUDGET: Record<string, number> = {}

/** The rows of pressed toggles in a source: each `role="group"` element with an `aria-pressed`
 *  button inside it. The element runs from its opening tag to the close that balances it. */
export function toggleRows(src: string): number {
  let rows = 0
  for (const m of src.matchAll(/role="group"/g)) {
    const open = src.lastIndexOf('<', m.index)
    const tag = /^<([\w.]+)/.exec(src.slice(open))?.[1]
    if (!tag) continue
    const name = tag.replace('.', '\\.')
    const edge = new RegExp(`<${name}[\\s>]|</${name}>`, 'g')
    edge.lastIndex = open + 1
    let depth = 1
    let end = src.length
    for (let t = edge.exec(src); t; t = edge.exec(src)) {
      depth += t[0].startsWith('</') ? -1 : 1
      if (depth === 0) { end = t.index; break }
    }
    if (/aria-pressed=/.test(src.slice(open, end))) rows += 1
  }
  return rows
}

/** The page-level controls a page draws outside its header. */
function outsideTheHeader(abs: string): number {
  const src = strip(readSource(abs))
  const bars = count(src, /<ListControls[\s>]/g) + count(src, /<FilterMenu[\s>]/g) + count(src, /<FilterChip[\s>]/g)
  if (!/<TopBar[\s>]/.test(src)) return bars
  const own = SECTION_CONTROLS[rel(abs)] ?? {}
  const strips = Math.max(0, count(src, /<Segmented[\s>]/g) - (own.segmented ?? 0))
  const searches = Math.max(0, count(src, /<SearchField[\s>]/g) - (own.search ?? 0))
  const toggles = Math.max(0, toggleRows(src) - (own.toggles ?? 0))
  return bars + strips + searches + toggles
}

describe('every page-level control is in the page header', () => {
  it('no page draws one outside it, beyond its budget', () => {
    const files = pageFiles()
    expect(files.length, 'the pages must be readable').toBeGreaterThan(100)
    const over = files
      .map((f) => [rel(f), outsideTheHeader(f)] as const)
      .filter(([f, n]) => n > (BUDGET[f] ?? 0))
      .map(([f, n]) => `${f}: ${n} > ${BUDGET[f] ?? 0}`)
    expect(over, 'put it in the header: a HeaderSearch, the one HeaderSegmented, or a HeaderFilter section').toEqual([])
  })

  it('and the budget carries no slack: a page that moved its controls takes its row out', () => {
    const stale = Object.entries(BUDGET)
      .map(([f, n]) => [f, n, outsideTheHeader(join(SRC, f))] as const)
      .filter(([, n, now]) => now < n)
      .map(([f, n, now]) => `${f}: budget ${n}, now ${now}`)
    expect(stale).toEqual([])
  })

  it('each named section control is still where its reason says', () => {
    for (const [f, own] of Object.entries(SECTION_CONTROLS)) {
      const src = strip(readSource(join(SRC, f)))
      expect(count(src, /<Segmented[\s>]/g), `${f}: ${own.why}`).toBeGreaterThanOrEqual(own.segmented ?? 0)
      expect(count(src, /<SearchField[\s>]/g), `${f}: ${own.why}`).toBeGreaterThanOrEqual(own.search ?? 0)
      expect(toggleRows(src), `${f}: ${own.why}`).toBeGreaterThanOrEqual(own.toggles ?? 0)
    }
  })

  it('counts a row of pressed toggles, and a group of anything else not', () => {
    const row = '<div role="group" aria-label="Filter by tag">\n  <button aria-pressed={on}>a</button>\n</div>'
    expect(toggleRows(row)).toBe(1)
    expect(toggleRows('<div role="group" aria-label="Body"><div><p>x</p></div></div><button aria-pressed={on} />')).toBe(0)
    expect(toggleRows(`<section role="group"><div><div /></div>${row}</section>`)).toBe(2)
  })

  it('and the page layouts keep no slot for a bar under the header', () => {
    for (const f of ['ui/WorkbenchLayout.tsx', 'pages/capabilities/ListBesideDetail.tsx']) {
      expect(strip(readSource(join(SRC, f))), `${f}: a controls slot is a bar under the header`).not.toMatch(/\bcontrols\??:/)
    }
  })
})

describe('the header holds one view switch, and a filter that says what is set', () => {
  const sources = () => [...pageFiles(), ...filesUnder(join(SRC, 'app'), (n) => /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n))]
    .map((f) => [rel(f), strip(readSource(f))] as const)

  it('no header cluster holds a second HeaderSegmented', () => {
    const twice: string[] = []
    for (const [f, src] of sources()) {
      for (const m of src.matchAll(/<HeaderActions[\s>][\s\S]*?<\/HeaderActions>/g)) {
        const n = count(m[0], /<HeaderSegmented[\s>]/g)
        if (n > 1) twice.push(`${f}: ${n} view switches in one header`)
      }
    }
    expect(twice, 'one HeaderSegmented, the main view; every other choice set is a HeaderFilter section').toEqual([])
  })

  it('no page writes a HeaderFilter label of its own', () => {
    // The label is composed from the sections' chosen options (`headerFilterLabel`), so it always
    // names what is set; a page can choose only the word before them (`prefix`).
    const own = sources().filter(([, src]) => /<HeaderFilter\b[^>]*\slabel=/.test(src)).map(([f]) => f)
    expect(own).toEqual([])
  })
})
