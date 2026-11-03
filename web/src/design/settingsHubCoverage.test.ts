import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The settings hub is the only navigation, so it must cover every subpage ───────────────────
//
// Since #5897 the hub draws one card per PAGE (`settingsPages.ts`, seventeen), and a page holds
// one or more SECTIONS (`SECTIONS` in `SettingsPage.tsx`, forty: each was a page of its own until
// then). A section reaches the hub through its widget in `SETTINGS_WIDGETS`: a part of its page's
// card, or a link on it, and in either case its text in the card's search haystack. So the rule
// below is the one it always was, one level down: no section without a widget, no widget without a
// section, and one name for each. That every section is held by a page is
// `pages/settings/everySettingStaysReachable.test.tsx`'s to pin.
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
// here, so the VACUITY test below fails by name — "the SECTIONS matcher must find the panels" —
// with the others still reported. The obvious alternative (throw inside the helper, which runs
// in the `describe` body) makes vitest print `Test Files 1 failed` and **`Tests  no tests`**:
// a real failure that reads as clean to anyone scanning the test COUNT. Measured both ways.

/** `{ id, label }` per top-level `SECTIONS` entry, scoped to that array so an unrelated
 *  `{ id: … }` elsewhere in the file cannot inflate the population. */
function sections(): { id: string; label: string }[] {
  const src = codeOf('src/pages/settings/SettingsPage.tsx')
  const block = src.match(/export const SECTIONS: SettingsSection\[\] = \[([\s\S]*?)\n\]/)
  return [...(block?.[1] ?? '').matchAll(/^\s*\{ id: '([a-z-]+)', label: '([^']*)'/gm)]
    .map((m) => ({ id: m[1], label: m[2] }))
}

/** `{ id, label }` per top-level `SETTINGS_WIDGETS` entry. Each ships those keys on one line, which
 *  is what makes the scan structural rather than a character window. */
function widgets(): { id: string; label: string }[] {
  const src = codeOf('src/pages/settings/settingsWidgets.tsx')
  const block = src.match(/export const SETTINGS_WIDGETS: SettingsWidget\[\] = \[([\s\S]*)/)
  return [...(block?.[1] ?? '').matchAll(/^\s*id: '([a-z-]+)', (?:group: '[^']+', )?label: '([^']*)'/gm)]
    .map((m) => ({ id: m[1], label: m[2] }))
}

/** Sections deliberately kept OFF the hub, each with the reason it is off.
 *
 *  🔑 PINNED AT ZERO, and that pin is the point. The escape hatch exists because one case would
 *  justify it — a panel that is genuinely empty or inert, where a card would be an on-ramp to
 *  nothing — but using it must be a reviewed decision, not the cheap way to make this rail green.
 *  So `EXCLUDED.size` is asserted below: adding an entry to silence a failure fails a DIFFERENT
 *  assertion, in the same run, and the PR that adds one has to move the number on purpose. */
const EXCLUDED = new Map<string, string>([
])

describe('every settings section is reachable from the hub', () => {
  const subs = sections()
  const hub = widgets()

  it('finds both populations, and the floor only rises (VACUITY)', () => {
    expect(subs.length, 'the SECTIONS matcher must find the panels').toBeGreaterThanOrEqual(40)
    expect(hub.length, 'the SETTINGS_WIDGETS matcher must find the widgets').toBeGreaterThanOrEqual(40)
    expect(new Set(hub.map((w) => w.id)).size, 'duplicate widget id').toBe(hub.length)
    expect(new Set(subs.map((s) => s.id)).size, 'duplicate section id').toBe(subs.length)
  })

  it('no section is reachable only by typing its URL', () => {
    const orphans = subs.filter((s) => !hub.some((w) => w.id === s.id) && !EXCLUDED.has(s.id))
    expect(
      orphans.map((s) => `${s.id}  (${s.label})`),
      'These panels ship, and NOTHING on the settings hub opens them — no part or link on a card,\n' +
        'and no entry in the search index either, because the index is built from the widget. Add a\n' +
        'widget to SETTINGS_WIDGETS in web/src/pages/settings/settingsWidgets.tsx (borrow the title and\n' +
        "the one-line description from the panel's own PanelHeader), or exclude it with a reason:\n  " +
        orphans.map((s) => s.id).join('\n  '),
    ).toEqual([])
  })

  it('no widget opens a section that does not exist', () => {
    const dangling = hub.filter((w) => !subs.some((s) => s.id === w.id))
    expect(
      dangling.map((w) => `${w.id}  (${w.label})`),
      'These widgets navigate to a section with no panel — the click lands back on the hub:\n  ' +
        dangling.map((w) => w.id).join('\n  '),
    ).toEqual([])
  })

  it('the exclusion list is pinned, and holds nothing stale', () => {
    for (const [id, why] of EXCLUDED) {
      expect(subs.some((s) => s.id === id), `EXCLUDED holds '${id}', which is not a section`).toBe(true)
      expect(
        hub.some((w) => w.id === id),
        `'${id}' is excluded from the hub AND has a widget — one of the two is wrong`,
      ).toBe(false)
      expect(why.length, `'${id}' needs a real reason, not a shrug`).toBeGreaterThan(30)
    }
    expect(EXCLUDED.size, 'an on-ramp to nothing is worse than none — but excluding a REAL panel hides it').toBe(0)
  })

  it('a widget and its section heading call the panel the same thing', () => {
    const drift = hub
      .filter((w) => subs.some((s) => s.id === w.id && s.label !== w.label))
      .map((w) => `${w.id}: widget "${w.label}" vs section "${subs.find((s) => s.id === w.id)!.label}"`)
    expect(drift, 'the widget and the section heading must agree').toEqual([])
  })

  it('every widget opens its OWN section', () => {
    const src = codeOf('src/pages/settings/settingsWidgets.tsx')
    const wrong: string[] = []
    for (const w of hub) {
      const at = src.search(new RegExp(`id: '${w.id}', (?:group: '[^']+', )?label: `))
      const next = hub[hub.indexOf(w) + 1]
      const end = next ? src.search(new RegExp(`id: '${next.id}', (?:group: '[^']+', )?label: `)) : src.length
      const body = src.slice(at, end > at ? end : src.length)
      if (!body.includes(`go('${w.id}')`)) wrong.push(`${w.id}: no go('${w.id}') in its own render`)
    }
    expect(wrong, 'a widget must navigate to the section it names').toEqual([])
  })
})
