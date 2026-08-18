import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { MoreRow } from './MoreRow'

// ── A list under a label that states the total must say what it is not showing ───────────────────
//
// Measured across the tree: **29 caps on a rendered list.** Eight of them sat under a label carrying
// the FULL count — `Relations · 47` above thirty rows, `Chats · 12` above eight — so the header was
// honest, the list was honest, and nothing reconciled them. A reader who trusts the header reads the
// list as all of it.
//
// 🪤 THE CAP IS NOT THE DEFECT, and the three sites that DID disclose spelled the sentence three
// different ways — `…{n} more`, `… {n} more`, `+{n} more`. That is what happens when a shared sentence
// is rewritten at every site, so all eleven now render one component.
//
// The remaining silent caps are a DIFFERENT problem and are deliberately untouched: they state no
// total at all (dashboard widget previews, row tag chips, a JSON tree). Adding a total is a copy
// decision per surface, not a mechanical one, and this census does not claim them.

const SRC = join(process.cwd(), 'src')
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
  })

const CAP = /([A-Za-z_$][\w$.?!\[\]]*)\s*\.slice\(\s*0\s*,\s*(\d+)\s*\)\s*\.map\(/g

/** Every capped-and-mapped list in the tree, with the cap and whether a label states its total. */
function cappedLists() {
  const out: Array<{ rel: string; base: string; root: string; cap: number; totalStated: boolean; after: string }> = []
  for (const abs of walk(SRC)) {
    const src = strip(readFileSync(abs, 'utf8'))
    const lines = src.split('\n')
    for (const m of src.matchAll(CAP)) {
      const ln = src.slice(0, m.index!).split('\n').length - 1
      const root = m[1].split('[')[0].split('.')[0].replace(/[!?]/g, '')
      const before = lines.slice(Math.max(0, ln - 8), ln + 1).join('\n')
      const totalStated = new RegExp(
        `(?:label|title)=\\{?[\`"'][^\`"']*\\$\\{[^}]*${root}[\\w$.?!\\[\\]]*\\.length`,
      ).test(before)
      out.push({
        rel: abs.replace(SRC + '/', ''), base: m[1], root, cap: Number(m[2]), totalStated,
        after: lines.slice(ln, ln + 24).join('\n'),
      })
    }
  }
  return out
}

/** The `<MoreRow>` that belongs to THIS list — matched by its `total` naming the same collection.
 *
 *  🪤 THE FIRST DRAFT JUST LOOKED FOR "a MoreRow within 24 lines", and a mutation exposed it:
 *  deleting the `relations` row made the check pair that list with the NEXT list's row
 *  (`shown={15}`), so it failed as a cap mismatch instead of as a missing row. Two adjacent lists
 *  sharing a cap would have made it pass outright — the neighbour's row standing in for the deleted
 *  one. A residue row has to be tied to the list it describes. */
function rowFor(list: { root: string; after: string }): string | null {
  for (const m of list.after.matchAll(/<MoreRow[\s\S]{0,140}?\/>/g)) {
    if (new RegExp(`total=\\{[^}]*\\b${list.root}\\b`).test(m[0])) return m[0]
  }
  return null
}

describe('MoreRow', () => {
  it('says nothing when nothing is hidden', () => {
    const { container } = render(<MoreRow total={6} shown={6} />)
    expect(container.textContent, 'a full list gets no residue line').toBe('')
    expect(render(<MoreRow total={2} shown={8} />).container.textContent,
      'and a list shorter than its own cap cannot hide anything').toBe('')
  })

  it('names the residue, not the total', () => {
    // The number the header already gives is the total; the one it cannot give is what is missing.
    render(<MoreRow total={47} shown={30} />)
    expect(screen.getByText('… 17 more')).toBeTruthy()
  })

  it('is one wording, so eleven sites cannot drift again', () => {
    const src = readFileSync(join(SRC, 'ui/MoreRow.tsx'), 'utf8')
    expect((src.match(/more</g) ?? []).length, 'exactly one place spells it').toBe(1)
  })
})

describe('every list whose label states a total discloses its cap', () => {
  it('the census finds them, and none is silent', () => {
    const lists = cappedLists()
    expect(lists.length, 'the sweep must find the capped lists').toBeGreaterThanOrEqual(25)
    const stated = lists.filter((l) => l.totalStated)
    expect(stated.length, 'and the label-states-a-total subset').toBeGreaterThanOrEqual(8)
    const silent = stated.filter((l) => !rowFor(l)).map((l) => `${l.rel} (${l.base})`)
    expect(silent, 'a label that promises N above a list of fewer owes the difference').toEqual([])
  })

  it('every MoreRow `shown` equals the cap actually applied', () => {
    // 🪤 THE DRIFT THIS REFACTOR INTRODUCES. The cap is now written twice per site — once in
    // `.slice(0, N)` and once as `shown={N}` — so `shown={8}` above a `slice(0, 6)` would compute a
    // residue of the wrong size and state it with total confidence. Nothing in the types can catch it.
    let checked = 0
    for (const l of cappedLists()) {
      const row = rowFor(l)
      const m = row?.match(/shown=\{(\d+)\}/)
      if (!m) continue
      checked++
      expect(Number(m[1]), `${l.rel}: the ${l.root} row's shown must match .slice(0, ${l.cap})`).toBe(l.cap)
    }
    expect(checked, 'the scan must actually pair some of them').toBeGreaterThanOrEqual(9)
  })

  it('no site spells the sentence for itself any more', () => {
    const adhoc: string[] = []
    for (const abs of walk(SRC)) {
      if (abs.endsWith('MoreRow.tsx')) continue
      const src = strip(readFileSync(abs, 'utf8'))
      // A residue written inline: a `.length - <cap>` beside the word "more".
      for (const line of src.split('\n')) {
        const at = line.search(/\.length\s*-\s*\d+\}?\s*more/)
        if (at < 0) continue
        // 🪤 A residue inside a TEMPLATE STRING is a different thing: `ChatPage` composes
        // `"a, b, c +4 more"` as one sentence for a summary line. It already discloses, a JSX
        // component cannot live in a template literal, and "… 4 more" reads wrong appended to a
        // comma-joined list. Convergence stops where the grammar changes.
        //
        // Detected by "there is a backtick to the left", NOT by backtick PARITY — the first draft
        // counted them and got an even number on exactly the line it needed to skip, because that
        // line NESTS a template inside a `${…}`, so the inner opener reads as the outer's closer.
        const insideTemplate = line.slice(0, at).includes('`')
        if (insideTemplate) continue
        adhoc.push(`${abs.replace(SRC + '/', '')}: ${line.trim().slice(0, 70)}`)
      }
    }
    expect(adhoc, 'three spellings of one sentence is how it drifted the first time').toEqual([])
  })

  it('an EXPANDING control is not a residue line — left alone deliberately', () => {
    // `CodeCockpitPage` renders `+{hidden} more` as a BUTTON that reveals the rest. That is a
    // disclosure the user can act on, not a statement that something is missing, so converging it
    // onto a static row would remove a feature.
    const src = strip(readFileSync(join(SRC, 'pages/code/CodeCockpitPage.tsx'), 'utf8'))
    expect(src, 'still a button').toMatch(/title=\{`Show \$\{hidden\} more file\$\{[^}]*\}`\}>\+\{hidden\} more<\/button>/)
  })

  it('a string SUMMARY keeps its own grammar', () => {
    // Pinned so the exclusion above is a judgement on record rather than a hole: this one already
    // tells the user what it omits, in the only form that reads correctly inline.
    const src = strip(readFileSync(join(SRC, 'pages/ChatPage.tsx'), 'utf8'))
    expect(src, 'the tool-name summary still discloses its own residue').toMatch(
      /toolNames\.slice\(0, 3\)\.join\(', '\)[\s\S]{0,60}toolNames\.length - 3\} more/,
    )
  })
})
