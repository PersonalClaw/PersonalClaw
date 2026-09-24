/** A surface that lets you FAVORITE something must let you see and use your favorites.
 *
 * `api.ts` states the contract itself, next to the inbox field:
 *
 *   // P11: user-favorited (a strong engagement signal + a star in the UI).
 *
 * The signal half shipped. The star did not. Measured on a real instance: four inbox items were
 * favorited, none of them distinguishable from the other forty — no star in the list, no favorites
 * filter, no count. The only thing in the whole frontend reading `favorited` for an inbox item was
 * the label of the button the user had just pressed, in the panel they were already looking at. One
 * of the four was `status: dismissed` — starred and dismissed at once, with nothing to reveal the
 * contradiction (issue 620).
 *
 * The flag was not inert: `handlers_inbox.py` records a `favorite` engagement signal and `_rank_items`
 * can consume it. So it worked for the ranker and not for the person who set it, which is the worse
 * shape — a control that does something invisible is harder to notice than one that does nothing.
 *
 * Knowledge had already been through this. Its own comment says so:
 *
 *   // Without this, favoriting was WRITE-ONLY — you could star an item and then had no
 *
 * That is the same bug, found and fixed once, on one surface. This rail is what makes the second fix
 * the last one: it asks, for every area that WRITES the field, whether that area also READS it.
 *
 * 🪤 THE FAKE VERSION OF THIS TEST asserts that `InboxPage.tsx` contains the string `favorited`.
 * That passes forever, and it would have passed on the broken code too if the page had merely
 * mentioned the field — and it says nothing about the next surface to grow a favorite button, which
 * is the whole failure mode. The rail has to DISCOVER the writers and check each one.
 */

import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const SRC = join(process.cwd(), 'src')
const PAGES = join(SRC, 'pages')

/** Any `api.*Favorit*(…)` call — the WRITE half, whatever the endpoint is named. Deliberately
 *  loose on the method name: `favoriteInboxItem` and `setKnowledgeFavorited` are the two spellings
 *  in the tree today and a third will not match either. */
const FAVORITE_WRITE = /\bapi\.[A-Za-z]*[Ff]avorit[A-Za-z]*\s*\(/

/** The accessible name a favorited row's star must carry. ONE spelling across every surface: the
 *  star is non-interactive and reports STATE, so "Favorite" would read as an action a screen-reader
 *  user would try to activate. Knowledge said "Favorite" and was aligned to this. */
const STAR_NAME = 'aria-label="Favorited"'

function walk(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

/** Page AREAS (the directory directly under `pages/`) that write the favorite flag. */
function areasThatWrite(): Map<string, string[]> {
  const byArea = new Map<string, string[]>()
  for (const file of walk(PAGES)) {
    if (!FAVORITE_WRITE.test(readFileSync(file, 'utf8'))) continue
    const rel = relative(PAGES, file).split(/[\\/]/)
    const area = rel[0]
    byArea.set(area, [...(byArea.get(area) ?? []), file])
  }
  return byArea
}

/** Everything in an area, so the READ can live in a sibling file — the write is usually in the
 *  detail panel and the star in the list page, which is correct and must not be penalised. */
const areaSource = (area: string) =>
  walk(join(PAGES, area)).map((f) => readFileSync(f, 'utf8')).join('\n')

describe('a favorite write implies a favorite read', () => {
  const writers = areasThatWrite()

  it('found the writers it is supposed to be checking', () => {
    // The vacuity floor. A renamed api method, a moved `pages/`, or a broken walk all produce an
    // empty map, and an empty map satisfies every assertion below.
    expect([...writers.keys()].sort()).toEqual(['inbox', 'knowledge'])
    // And the pattern must match what it is looking for, or the discovery is measuring nothing.
    expect(FAVORITE_WRITE.test('api.favoriteInboxItem(item.id, true)')).toBe(true)
    expect(FAVORITE_WRITE.test('api.setKnowledgeFavorited(it.id, next)')).toBe(true)
    expect(FAVORITE_WRITE.test('api.listInboxItems()')).toBe(false)
  })

  it.each([...areasThatWrite().keys()])('%s shows a favorited row in its list', (area) => {
    const source = areaSource(area)
    expect(source).toContain(STAR_NAME)
  })

  it.each([...areasThatWrite().keys()])('%s lets you filter to favorites', (area) => {
    const source = areaSource(area)
    // Three things, and the third is the one that matters. A predicate and a count with no control
    // to select them is a filter nobody can reach — which is the same dead-control shape as the
    // star that led nowhere. Falsified: deleting the filter OPTION while leaving the predicate and
    // the count in place kept an earlier version of this leg green.
    expect(source, 'no favorites predicate/key').toMatch(/'favorites'/)
    expect(source, 'no favorites count').toMatch(/favorited\)\s*\.length|filterCount\('favorites'\)|counts\.favorites/)
    // The user-visible label, capitalised as a control's text — the part a person can actually
    // click. Both surfaces render it: `label: 'Favorites'` in a filter section, and
    // `<Star /> Favorites {count}` in a chip.
    expect(source, 'no selectable Favorites control').toMatch(/\bFavorites\b/)
  })

  it('uses ONE accessible name for the FAVORITED star, everywhere', () => {
    // Two spellings of the same state is the drift this project treats as a defect: a user who
    // learns the word on one page must find it on the next. Knowledge said "Favorite" — an action
    // name on a static icon — and was aligned rather than allowlisted.
    //
    // Scoped to stars guarded by `favorited`, NOT to every `<Star>` in the tree. The first version
    // of this leg checked all of them and failed on `aria-label="Active project"` — a star icon
    // reused for an unrelated meaning, which is legitimate. A rail that claims every instance of a
    // glyph means one thing is wrong about the codebase, not about the codebase's naming.
    // Matched on a CONDITIONALLY RENDERED star — `favorited && <Star` — because that is the shape
    // where the icon is the only thing conveying the state, so it needs a name.
    //
    // Deliberately NOT every star whose styling varies with the flag. `InboxDetail` renders one
    // inside a Button whose own text already reads "Favorited"/"Favorite"; giving that icon an
    // aria-label would append to the button's accessible name, so the correct markup there is an
    // unnamed icon. The first version of this leg flagged it, which would have pushed a real a11y
    // regression in the name of consistency.
    const INDICATOR = /favorited\s*&&\s*<Star\b/
    const names: string[] = []
    for (const file of walk(PAGES)) {
      for (const line of readFileSync(file, 'utf8').split('\n')) {
        if (!INDICATOR.test(line)) continue
        const m = line.match(/aria-label="([^"]+)"/)
        // An unnamed indicator IS a failure: nothing else on the row says the item is favorited.
        names.push(m ? m[1] : '(indicator with no accessible name)')
      }
    }
    expect(names.length).toBeGreaterThan(1)
    expect([...new Set(names)]).toEqual(['Favorited'])
  })
})
