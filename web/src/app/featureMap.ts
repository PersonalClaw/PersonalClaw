/** The feature map: every page the router serves, with how a person reaches it (#3812).
 *
 *  One row per route: what it opens, where that lives in the sidebar (a row of its own, a tab of a
 *  row, the phone's drawer, or nowhere), the palette rows that open it, and the source files that
 *  link to it. The routes that none of those reach, so only a typed address opens them, are listed
 *  last as not findable. `docs/feature-map.md` is this, generated (`npm run docs:feature-map`), and
 *  `featureMap.test.tsx` fails when the committed file and what the app holds differ.
 *
 *  Pure: the shell's tables, the palette's rows and the source's links are read by the test and
 *  handed in, so the builder is checked against planted inputs as readily as the real ones. */

/** A sidebar row, as `App.tsx`'s `NAV` declares it. */
export interface SidebarRow {
  readonly id: string
  readonly label: string
  /** The header it sits under, when it has one. */
  readonly section?: string
}

/** A destination's tabs, each at its own address. */
export interface DestinationTabs {
  /** The sidebar row the tabs belong to. */
  readonly row: string
  readonly tabs: readonly { readonly label: string; readonly address: string }[]
}

/** A palette row that opens a page. */
export interface PaletteRow {
  readonly label: string
  /** The row's section, and its keys when it has any ("Go to", "Action · ⌘`"). */
  readonly hint?: string
  /** The page it opens, without `#/` or its query. */
  readonly page: string
}

export interface FeatureMapInput {
  /** The router's routes, each with the page it opens (a route that lands elsewhere names where). */
  readonly routes: readonly { readonly route: string; readonly page: string }[]
  readonly sidebar: readonly SidebarRow[]
  /** The rows only the phone's drawer carries. */
  readonly drawerOnly: readonly SidebarRow[]
  readonly tabs: readonly DestinationTabs[]
  readonly palette: readonly PaletteRow[]
  /** The files that link to each page, by page. */
  readonly links: Readonly<Record<string, readonly string[]>>
}

export interface FeatureRow {
  readonly route: string
  readonly opens: string
  readonly sidebar: string | null
  readonly palette: readonly string[]
  readonly linkedFrom: readonly string[]
}

/** How many linking files a row names before it counts the rest. */
const LINKS_SHOWN = 4

/** A palette row as the map names it: its words, and its hint when that says more than the section. */
const paletteWords = (row: PaletteRow) => (row.hint && row.hint !== 'Go to' ? `${row.label} (${row.hint})` : row.label)

/** Each route's row: what it opens and every way to it. */
export function featureRows(input: FeatureMapInput): FeatureRow[] {
  const rowOf = new Map(input.sidebar.map((r) => [r.id, r]))
  const drawerOf = new Map(input.drawerOnly.map((r) => [r.id, r]))
  const tabOf = new Map<string, { row: SidebarRow | undefined; label: string }>()
  for (const t of input.tabs) {
    for (const tab of t.tabs) {
      const page = tab.address.split('?')[0]
      if (!tabOf.has(page)) tabOf.set(page, { row: rowOf.get(t.row), label: tab.label })
    }
  }
  const seen = new Set<string>()
  const out: FeatureRow[] = []
  for (const { route, page } of input.routes) {
    if (seen.has(route)) continue
    seen.add(route)
    const row = rowOf.get(page)
    const tab = row ? undefined : tabOf.get(page)
    const drawer = row || tab ? undefined : drawerOf.get(page)
    const sidebar = row ? (row.section ? `${row.section} › ${row.label}` : row.label)
      : tab?.row ? `${tab.row.label}, its ${tab.label} tab`
        : drawer ? `${drawer.label}, in the phone's drawer only`
          : null
    const palette = [...new Set(input.palette.filter((p) => p.page === page).map(paletteWords))].sort()
    const firstRow = input.palette.find((p) => p.page === page)?.label.replace(/^(Go to|Open) /, '')
    out.push({
      route,
      opens: row?.label ?? tab?.label ?? drawer?.label ?? firstRow ?? page,
      sidebar,
      palette,
      linkedFrom: [...(input.links[page] ?? [])].sort(),
    })
  }
  return out
}

/** The routes only a typed address opens: no sidebar row or tab, no palette row and no link. */
export function notFindable(rows: readonly FeatureRow[]): string[] {
  return rows.filter((r) => !r.sidebar && !r.palette.length && !r.linkedFrom.length).map((r) => r.route)
}

const cell = (s: string) => s.replace(/\|/g, '\\|')

function linkedCell(files: readonly string[]): string {
  if (!files.length) return 'none'
  const shown = files.slice(0, LINKS_SHOWN).map((f) => `\`${f}\``).join(', ')
  return files.length > LINKS_SHOWN ? `${shown} and ${files.length - LINKS_SHOWN} more` : shown
}

/** One route's line of the table. */
export function featureLine(r: FeatureRow): string {
  return `| \`#/${r.route}\` | ${cell(r.opens)} | ${cell(r.sidebar ?? 'not in the sidebar')} | ${cell(r.palette.join('; ') || 'none')} | ${linkedCell(r.linkedFrom)} |`
}

/** The whole document. */
export function featureMapMarkdown(input: FeatureMapInput): string {
  const rows = featureRows(input)
  const lost = notFindable(rows)
  return [
    '# Feature map',
    '',
    '<!-- Generated by `npm run docs:feature-map` (web/src/app/featureMap.test.tsx). Do not edit by hand. -->',
    '',
    'Every page the router serves, and how a person reaches it: where it lives in the sidebar, the',
    'command palette rows that open it, and the source files that link to it. A route that lands',
    'somewhere else is listed by where it lands. Read from `web/src/app/App.tsx` (the sidebar\'s rows),',
    'the shipped-route catalogue (`web/src/app/shippedRoutes.ts`, the router\'s routes), the palette\'s',
    'rows as the shell hands them over, and the source\'s links. `featureMap.test.tsx` fails when this',
    'file and the app differ.',
    '',
    '| Route | Opens | In the sidebar | Palette | Linked from |',
    '|---|---|---|---|---|',
    ...rows.map(featureLine),
    '',
    '## Not findable',
    '',
    'Routes only a typed address opens: no sidebar row or tab, no palette row and no link.',
    '',
    ...(lost.length ? lost.map((r) => `- \`#/${r}\``) : ['None.']),
    '',
  ].join('\n')
}
