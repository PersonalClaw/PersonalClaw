import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The settings hub is the only navigation, so it must cover every subpage ───────────────────
//
// `#/settings` renders `SettingsHome`, which renders `SETTINGS_WIDGETS` and holds NO second list.
// Every panel lives at `#/settings/<id>` and mounts only when you navigate there. So a `SUBPAGES`
// entry with no widget is reachable **only by typing its URL** — and invisible to the settings
// search too, because that haystack is `label` + `description` + `useSearchText()`, all three of
// which live on the widget (`SettingsHome.tsx`'s `Cell`).
//
// Measured on `origin/main` when this rail was written: **34 subpages, 30 widgets**, and the four
// with no card were `ambient`, `companion`, `sources` and `packs`. Derived both directions — set
// difference over the two id lists — so the reverse (a card for a panel that does not exist) was
// checked at the same time and was **empty**.
//
// Verified as genuinely unreachable, not merely card-less, with the grep in its ROUTE form rather
// than its prose form: `git grep -E "'settings/(ambient|companion|sources|packs)'" -- web` (the
// quotes matter — that is how `go()`/`navigate()` spell a route) returns **zero hits** across the
// whole `web` tree. The unquoted form returns 11, and all 11 are prose: one `DiscoverPage` comment,
// one `PacksPanel` doc comment, and 9 lines inside six test files' comments. So no other surface
// deep-linked them either. Four whole settings panels that a user could not arrive at.
//
// This is the sibling of `settingsSubpageCoverage.test.ts`, which pins the same `SUBPAGES` list
// against the axe manifest in `e2e/routes.ts`. Two different consequences of the same drift:
// a panel missing from the manifest is never SCANNED, a panel missing from the hub is never
// REACHED. (Worth stating: the four above were already in `SETTINGS_PANELS`, so CI had been
// axe-scanning routes that no click could get to.)
//
// 🪤 DECLARATION-vs-DECLARATION by necessity, like its sibling: `SUBPAGES` is an array of objects
// holding JSX renderers and `SETTINGS_WIDGETS` is an array of objects holding hooks, so neither
// can be imported into a plain unit test without dragging the whole panel tree in. Both are read
// from source and the assertion is that they agree. What EXERCISES them is `npm run e2e:a11y`
// (every route) and the browser drive in the PR that added this file.
//
// 🪤 COMMENTS ARE STRIPPED FIRST. This repo has now had a ratchet count its own prose as code at
// least five times (the primitive-adoption ratchet twice over a `<button>` in a comment; the
// load-error scanner over a `.catch` it was documenting). The widgets file's own header comment
// names the four ids this rail exists for, so an unstripped scan would have read them as entries.

const WEB = process.cwd()
const codeOf = (rel: string) =>
  readFileSync(join(WEB, rel), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

// 🪤 NEITHER HELPER ASSERTS, AND THAT IS DELIBERATE. A matcher that stops resolving returns `[]`
// here, so the VACUITY test below fails by name — "the SUBPAGES matcher must find the panels" —
// with the other six still reported. The obvious alternative (throw inside the helper, which runs
// in the `describe` body) makes vitest print `Test Files 1 failed` and **`Tests  no tests`**:
// a real failure that reads as clean to anyone scanning the test COUNT. Measured both ways.

/** `{ id, label }` per top-level `SUBPAGES` entry, scoped to that array so an unrelated
 *  `{ id: … }` elsewhere in the file cannot inflate the population. */
function subpages(): { id: string; label: string }[] {
  const src = codeOf('src/pages/settings/SettingsPage.tsx')
  const block = src.match(/const SUBPAGES: SubPage\[\] = \[([\s\S]*?)\n\]/)
  return [...(block?.[1] ?? '').matchAll(/^\s*\{ id: '([a-z-]+)', label: '([^']*)'/gm)]
    .map((m) => ({ id: m[1], label: m[2] }))
}

/** `{ id, group, label }` per top-level `SETTINGS_WIDGETS` entry. Each ships those three keys on
 *  one line, which is what makes the scan structural rather than a character window. */
function widgets(): { id: string; group: string; label: string }[] {
  const src = codeOf('src/pages/settings/settingsWidgets.tsx')
  const block = src.match(/export const SETTINGS_WIDGETS: SettingsWidget\[\] = \[([\s\S]*)/)
  return [...(block?.[1] ?? '').matchAll(/^\s*id: '([a-z-]+)', group: '([^']+)', label: '([^']*)'/gm)]
    .map((m) => ({ id: m[1], group: m[2], label: m[3] }))
}

/** Subpages deliberately kept OFF the hub, each with the reason it is off.
 *
 *  🔑 PINNED AT ZERO, and that pin is the point. The escape hatch exists because one case would
 *  justify it — a panel that is genuinely empty or inert, where a card would be an on-ramp to
 *  nothing — but using it must be a reviewed decision, not the cheap way to make this rail green.
 *  So `EXCLUDED.size` is asserted below: adding an entry to silence a failure fails a DIFFERENT
 *  assertion, in the same run, and the PR that adds one has to move the number on purpose.
 *
 *  All four panels this rail was written for were read before their cards were built, and none
 *  qualified: `AmbientPanel` ships 3 switches + 3 bounded numbers, `CompanionPanel` a switch, a
 *  name field, the live advertiser record and the PWA-install state, `SourcesPanel` 1 switch +
 *  5 numbers + the artifact mirror + the scratchpad path, `PacksPanel` 518 lines including the
 *  pack store and the installed ledger. Nothing to exclude. */
const EXCLUDED = new Map<string, string>([
  // (empty — see the note above before adding one)
])

/** The hub's group headings. `SettingsHome` derives its sections from this field, in registry
 *  order, so a typo does not fail anywhere — it mints a FIFTH heading with one lonely card
 *  under it. Pinned as a closed set; a real new group is a deliberate edit here. */
const GROUPS = new Set(['General', 'AI & Models', 'Workspace', 'System'])

describe('every settings subpage is reachable from the hub', () => {
  const subs = subpages()
  const hub = widgets()

  it('finds both populations, and the floor only rises (VACUITY)', () => {
    // Without this, a matcher that stopped resolving would make every assertion below trivially
    // green over two empty arrays — the failure mode a coverage rail cannot afford. 34 subpages
    // and 34 widgets when written; a ratchet, so adding a subpage moves both numbers up together.
    expect(subs.length, 'the SUBPAGES matcher must find the panels').toBeGreaterThanOrEqual(34)
    expect(hub.length, 'the SETTINGS_WIDGETS matcher must find the cards').toBeGreaterThanOrEqual(34)
    // And no id may appear twice: two widgets for one subpage renders the card twice.
    expect(new Set(hub.map((w) => w.id)).size, 'duplicate widget id').toBe(hub.length)
    expect(new Set(subs.map((s) => s.id)).size, 'duplicate subpage id').toBe(subs.length)
  })

  it('no subpage is reachable only by typing its URL', () => {
    const orphans = subs.filter((s) => !hub.some((w) => w.id === s.id) && !EXCLUDED.has(s.id))
    expect(
      orphans.map((s) => `${s.id}  (${s.label})`),
      'These panels ship, and NOTHING on the settings hub opens them — no card, and no entry in\n' +
        'the search index either, because the index is built from the widget. Add a widget to\n' +
        'SETTINGS_WIDGETS in web/src/pages/settings/settingsWidgets.tsx (borrow the title and the\n' +
        "one-line description from the panel's own PanelHeader), or exclude it with a reason:\n  " +
        orphans.map((s) => s.id).join('\n  '),
    ).toEqual([])
  })

  it('no card opens a subpage that does not exist', () => {
    // The other direction, and it fails silently rather than loudly: `SettingsPage` renders
    // `SUBPAGES.find((s) => s.id === sub)` and falls through to the HOME when that misses, so a
    // card for a deleted panel looks like a click that did nothing.
    const dangling = hub.filter((w) => !subs.some((s) => s.id === w.id))
    expect(
      dangling.map((w) => `${w.id}  (${w.label})`),
      'These hub cards navigate to a route with no panel — the click lands back on the hub:\n  ' +
        dangling.map((w) => w.id).join('\n  '),
    ).toEqual([])
  })

  it('the exclusion list is pinned, and holds nothing stale', () => {
    for (const [id, why] of EXCLUDED) {
      expect(subs.some((s) => s.id === id), `EXCLUDED holds '${id}', which is not a subpage`).toBe(true)
      expect(
        hub.some((w) => w.id === id),
        `'${id}' is excluded from the hub AND has a card — one of the two is wrong`,
      ).toBe(false)
      expect(why.length, `'${id}' needs a real reason, not a shrug`).toBeGreaterThan(30)
    }
    // 🔑 The pin. Raise this only in a PR whose body argues the exclusion.
    expect(EXCLUDED.size, 'an on-ramp to nothing is worse than none — but excluding a REAL panel hides it').toBe(0)
  })

  it('a card and its breadcrumb call the panel the same thing', () => {
    // The subpage header renders `Settings › <SUBPAGES label>`; the card shows the widget's
    // `label`. Two spellings means the thing you clicked is not the thing you landed on.
    const drift = hub
      .filter((w) => subs.some((s) => s.id === w.id && s.label !== w.label))
      .map((w) => `${w.id}: card "${w.label}" vs breadcrumb "${subs.find((s) => s.id === w.id)!.label}"`)
    expect(drift, 'the card and the breadcrumb must agree').toEqual([])
  })

  it('every card sits in one of the hub\'s real groups', () => {
    const stray = hub.filter((w) => !GROUPS.has(w.group)).map((w) => `${w.id}: '${w.group}'`)
    expect(stray, 'an unrecognised group mints its own heading with one card under it').toEqual([])
    // Vacuity on the pin itself: an unused group is a heading nobody ever sees.
    const used = new Set(hub.map((w) => w.group))
    expect([...GROUPS].filter((g) => !used.has(g)), 'GROUPS declares a heading no card uses').toEqual([])
  })

  it('every card opens its OWN subpage', () => {
    // A widget is ~30 lines and the fastest way to write one is to copy the neighbour, which puts
    // `go('<neighbour>')` inside it. The card then looks correct and opens the wrong panel.
    const src = codeOf('src/pages/settings/settingsWidgets.tsx')
    const wrong: string[] = []
    for (const w of hub) {
      const at = src.indexOf(`id: '${w.id}', group: '${w.group}'`)
      const next = hub[hub.indexOf(w) + 1]
      const end = next ? src.indexOf(`id: '${next.id}', group: '${next.group}'`) : src.length
      // Segment per widget, NOT a character window: the same over-wide-proximity flaw that made
      // `tileLoadFailure`'s first draft report 2 sites of 4.
      const body = src.slice(at, end > at ? end : src.length)
      if (!body.includes(`go('${w.id}')`)) wrong.push(`${w.id}: no go('${w.id}') in its own render`)
    }
    expect(wrong, 'a card must navigate to the subpage it names').toEqual([])
  })
})
