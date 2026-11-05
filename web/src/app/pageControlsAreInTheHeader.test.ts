// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { join, relative } from 'node:path'
import { filesUnder, readSource } from '../test/sourceTree'
import { isThePrimary } from '../ui/HeaderActions'

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
const SECTION_CONTROLS: Record<string, { segmented?: number; search?: number; toggles?: number; filterMenu?: number; why: string }> = {
  'pages/files/FilesSection.tsx': { search: 1, why: "the explorer pane's search of the file tree" },
  'pages/tools/ToolsPage.tsx': { segmented: 3, why: "the add and edit server dialogs' type and transport fields" },
  'pages/loop/LoopComposer.tsx': { segmented: 3, why: "the composer's own fields: granularity, project kind, loop kind" },
  'pages/loops/DesignCockpitPage.tsx': { segmented: 1, why: "the design preview pane's colour scheme" },
  'pages/loops/LoopPlanReview.tsx': { segmented: 1, toggles: 1, why: "plan fields: the goal type, and each phase's capabilities" },
  'pages/knowledge/SourceCreatePage.tsx': { segmented: 1, why: 'a form field, the enrichment for this source' },
  'pages/prompts/PromptCreatePage.tsx': { segmented: 2, why: "a form field (the prompt kind) and the editor's right-rail pane" },
  'pages/triggers/TriggerCreatePage.tsx': { segmented: 1, why: 'a form field, the trigger kind' },
  'pages/learning/LearningPage.tsx': { segmented: 1, why: "the Proposals section's own kind filter, under its heading" },
  'pages/dashboard/agents/AgentDay.tsx': { filterMenu: 1, why: "the agents' day's own Show filter, over that one panel of Home, beside its heading" },
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
  const own = SECTION_CONTROLS[rel(abs)] ?? {}
  const menus = Math.max(0, count(src, /<FilterMenu[\s>]/g) - (own.filterMenu ?? 0))
  const bars = count(src, /<ListControls[\s>]/g) + menus + count(src, /<FilterChip[\s>]/g)
  if (!/<TopBar[\s>]/.test(src)) return bars
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
      expect(count(src, /<FilterMenu[\s>]/g), `${f}: ${own.why}`).toBeGreaterThanOrEqual(own.filterMenu ?? 0)
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

// ── One order in every header cluster ─────────────────────────────────────────────────────────────
//
// The primary action is rightmost, so the eye finds it at the row's end on every page; a
// destructive control is the first of the actions, apart from the rest; a control that always
// lives in the `…` (`inOverflow`) has no place in the row and is left out. "The primary" is a
// `variant="primary"` control, or a `priority="primary"` one that does something rather than open a
// panel (a Session map toggle keeps its place by priority without being the page's action).
// Alternatives (Edit or Save, Start or Pause) are all primaries, so they may follow one another.

/** Whether a member's attributes make it the page's primary, the one the order puts last. */
export function railPrimary(attrs: string): boolean {
  return /variant="primary"/.test(attrs) || (/priority="primary"/.test(attrs) && !/ariaExpanded=/.test(attrs))
}

/** Where a cluster's members break the one order. */
export function headerOrderFindings(src: string): string[] {
  const out: string[] = []
  for (const block of src.matchAll(/<HeaderActions[\s>][\s\S]*?<\/HeaderActions>/g)) {
    const members = [...block[0].matchAll(/<(HeaderControl|HeaderMenu)\b([\s\S]*?)\/?>/g)]
      .map((m) => ({ attrs: m[2], label: /label=("[^"]*"|\{[^}]*\})/.exec(m[2])?.[1].slice(0, 40) ?? m[1] }))
      .filter((m) => !/\binOverflow\b/.test(m.attrs))
      .map((m) => ({
        ...m,
        primary: railPrimary(m.attrs),
        danger: /\sdanger(\s|$|\/)|variant="danger"/.test(m.attrs),
      }))
    const first = members.findIndex((m) => m.primary)
    if (first >= 0) {
      for (const m of members.slice(first)) if (!m.primary) out.push(`${m.label} after the primary`)
    }
    members.forEach((m, i) => {
      if (!m.danger) return
      const before = members.slice(0, i).filter((p) => !p.danger && !p.primary)
      if (before.length) out.push(`${m.label} (destructive) after ${before.map((p) => p.label).join(', ')}`)
    })
  }
  return out
}

describe('every header cluster keeps the one order', () => {
  const files = () => [...pageFiles(), ...filesUnder(join(SRC, 'app'), (n) => /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n)),
    ...filesUnder(join(SRC, 'ui'), (n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n))]

  it('the primary it counts is the one the cluster draws after the `…`', () => {
    // The cluster portals `isThePrimary` controls to its trailing slot; a primary this rail counts
    // that the cluster left in place sat before the `…` (Tools' Add tool server, the Inbox's
    // Capture a note).
    const cases: [string, string | undefined, string | undefined, boolean | undefined][] = [
      ['variant="primary"', 'primary', undefined, undefined],
      ['priority="primary"', undefined, 'primary', undefined],
      ['priority="primary" ariaExpanded={open}', undefined, 'primary', false],
      ['priority="low"', undefined, 'low', undefined],
      ['variant="danger" priority="low"', 'danger', 'low', undefined],
    ]
    for (const [attrs, variant, priority, expanded] of cases) {
      expect(railPrimary(attrs), attrs).toBe(isThePrimary(variant, priority, expanded))
    }
  })

  it('the primary last, and a destructive control before the other actions', () => {
    const found = files().flatMap((f) => headerOrderFindings(strip(readSource(f))).map((x) => `${rel(f)}: ${x}`))
    expect(found).toEqual([])
  })

  it('reads a cluster as the order rule says', () => {
    const cluster = (body: string) => `<HeaderActions>${body}</HeaderActions>`
    expect(headerOrderFindings(cluster('<HeaderControl label="Delete" danger /><HeaderControl label="Pin" /><HeaderControl label="Edit" variant="primary" />'))).toEqual([])
    expect(headerOrderFindings(cluster('<HeaderControl label="Start" priority="primary" /><HeaderControl label="Cancel" />'))).toEqual(['"Cancel" after the primary'])
    expect(headerOrderFindings(cluster('<HeaderControl label="Pin" /><HeaderControl label="Delete" danger priority="low" />'))).toEqual(['"Delete" (destructive) after "Pin"'])
    // A disclosure kept by priority is not the primary, and a control in the `…` has no place.
    expect(headerOrderFindings(cluster('<HeaderControl label="Map" priority="primary" ariaExpanded={o} /><HeaderControl label="Activity" />'))).toEqual([])
    expect(headerOrderFindings(cluster('<HeaderControl label="Go" variant="primary" /><HeaderControl label="New chat" inOverflow />'))).toEqual([])
    // Alternatives may follow one another.
    expect(headerOrderFindings(cluster('{a ? <HeaderControl label="Save" variant="primary" /> : <HeaderControl label="Edit" variant="primary" />}'))).toEqual([])
  })
})

// ── Every control in a header row is a member of its cluster ─────────────────────────────────────
//
// A control the cluster does not know of is not measured, so the row's tier and the title's room
// are worked out without it, and it cannot shed into the `…`: the Inbox's Digest, Artifacts'
// Deployed menu and pin, the workflow definition's and editor's buttons were hand-laid buttons in
// the row. Each child of a cluster is a member (`HeaderControl`, `HeaderMenu`, `HeaderPopover`,
// `HeaderSearch`, `HeaderFilter`, `HeaderSegmented`), or a component whose own source registers
// with it (`useHeaderChild`) or draws one.

const MEMBERS = new Set(['HeaderControl', 'HeaderMenu', 'HeaderPopover', 'HeaderSearch', 'HeaderFilter', 'HeaderSegmented'])
const DRAWS_A_MEMBER = /useHeaderChild\(|<(HeaderControl|HeaderMenu|HeaderPopover|HeaderSearch|HeaderFilter|HeaderSegmented)\b/

/** The non-member components a cluster draws, each with whether its own source joins the cluster. */
export function clusterStrangers(file: string, src: string, readModule: (from: string, spec: string) => string | null): string[] {
  const out: string[] = []
  const imports = new Map<string, string>()
  for (const m of src.matchAll(/import\s*\{([^}]*)\}\s*from\s*'([^']+)'/g)) {
    for (const name of m[1].split(',').map((n) => n.trim().split(/\s+as\s+/).pop()!).filter(Boolean)) imports.set(name, m[2])
  }
  for (const block of src.matchAll(/<HeaderActions[\s>][\s\S]*?<\/HeaderActions>/g)) {
    for (const tag of new Set([...block[0].matchAll(/<([A-Z][A-Za-z]*)\b/g)].map((m) => m[1]))) {
      if (tag === 'HeaderActions' || MEMBERS.has(tag)) continue
      const spec = imports.get(tag)
      const mod = spec ? readModule(file, spec) : src
      if (!mod || !DRAWS_A_MEMBER.test(mod)) out.push(`${tag} is drawn in a header row but joins no cluster`)
    }
  }
  return out
}

describe('every control in a header row is a member of its cluster', () => {
  const resolve = (from: string, spec: string): string | null => {
    if (!spec.startsWith('.')) return null
    const base = join(SRC, from, '..', spec)
    for (const ext of ['.tsx', '.ts', '/index.tsx']) {
      try { return strip(readSource(base + ext)) } catch { /* the next form */ }
    }
    return null
  }

  it('in every page and the shell', () => {
    const files = [...pageFiles(), ...filesUnder(join(SRC, 'app'), (n) => /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n))]
    const found = files.flatMap((f) => clusterStrangers(rel(f), strip(readSource(f)), resolve).map((x) => `${rel(f)}: ${x}`))
    expect(found).toEqual([])
  })

  it('reads a cluster as the rule says', () => {
    const page = (body: string) => `import { Popover } from './Popover'\nimport { Pin } from './Pin'\n<HeaderActions>${body}</HeaderActions>`
    const modules: Record<string, string> = { './Popover': 'export function Popover() {}', './Pin': 'export function Pin() { return <HeaderControl /> }' }
    const read = (_: string, spec: string) => modules[spec] ?? null
    expect(clusterStrangers('p.tsx', page('<HeaderControl label="A" /><Pin />'), read)).toEqual([])
    expect(clusterStrangers('p.tsx', page('<Popover trigger={() => <button>Digest</button>} />'), read))
      .toEqual(['Popover is drawn in a header row but joins no cluster'])
  })
})
