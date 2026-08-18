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

  it('names what is hidden when the caller says what it is', () => {
    // Rendered, not counted in the source: the previous check for "one spelling" catches a DELETED
    // interpolation only by accident (the regex stops matching), which is a weak reason for a test to
    // go red. This asserts the sentence.
    render(<MoreRow total={247} shown={200} noun="rows" />)
    expect(screen.getByText('… 47 more rows')).toBeTruthy()
  })

  it('stays subject-less where the list above it already says what these are', () => {
    const { container } = render(<MoreRow total={9} shown={6} />)
    expect(container.textContent?.trim(), 'no dangling noun, no guessed one').toBe('… 3 more')
  })

  it('is one wording, so eleven sites cannot drift again', () => {
    const src = readFileSync(join(SRC, 'ui/MoreRow.tsx'), 'utf8')
    // Matches the word as RENDERED — followed by the element's close or by the optional noun's
    // interpolation. (It keyed on `more<` until the noun prop arrived, at which point the only
    // spelling in the tree stopped matching and this failed on correct code: an assertion pinned to
    // the incidental shape of the markup rather than to the sentence.)
    expect((src.match(/\bmore(?:<|\{)/g) ?? []).length, 'exactly one place spells it').toBe(1)
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

  it('a truncated TABLE names what it dropped', () => {
    // 🔑 THE THIRD GROUP, and the sharpest of the three. A truncated LIST visibly stops; a truncated
    // TABLE looks complete — the frame closes, the header is intact, and nothing suggests the answer
    // continues. Worse, `ToolOutput` caps COLUMNS: a dropped field leaves a table that simply never
    // mentions six of your keys, and `overflow-x-auto` does not help because it scrolls only what was
    // rendered. Its own docstring said the quiet part already — "Columns = union of keys (capped)".
    const pins: Array<[string, RegExp]> = [
      ['pages/chat/toolRenderers/primitives.tsx', /<MoreRow total=\{body\.length\} shown=\{200\} noun="rows"/],
      ['pages/files/browse/FilePreviews.tsx', /<MoreRow total=\{body\.length\} shown=\{500\} noun="rows"/],
      ['pages/tools/ToolOutput.tsx', /<MoreRow total=\{cols\.length\} shown=\{8\} noun="columns"/],
    ]
    for (const [rel, re] of pins) {
      expect(strip(readFileSync(join(SRC, rel), 'utf8')), `${rel} must disclose its cap`).toMatch(re)
    }
  })

  it('a residue under a table is NAMED, because "… 6 more" would read as rows', () => {
    // The ambiguity is the whole reason the prop exists, so it is required exactly where a table is
    // involved and left off everywhere else (beneath a stacked list the subject is what sits above).
    for (const rel of ['pages/chat/toolRenderers/primitives.tsx', 'pages/files/browse/FilePreviews.tsx',
      'pages/tools/ToolOutput.tsx']) {
      const src = strip(readFileSync(join(SRC, rel), 'utf8'))
      for (const tag of src.match(/<MoreRow[\s\S]{0,160}?\/>/g) ?? []) {
        // FilePreviews also has a JSON-tree residue that is NOT under a table — it sits under an
        // indented node list, where the subject is unambiguous.
        const underTable = /noun=/.test(tag) || !/entries\.length/.test(tag)
        if (!underTable) continue
        expect(tag, `${rel}: a table residue needs its noun`).toMatch(/noun="(rows|columns)"/)
      }
    }
  })

  it('the row sits OUTSIDE the table — a div in a tbody is invalid markup', () => {
    // Not a style point: the browser hoists it out and the residue lands somewhere unpredictable.
    for (const rel of ['pages/chat/toolRenderers/primitives.tsx', 'pages/files/browse/FilePreviews.tsx',
      'pages/tools/ToolOutput.tsx']) {
      const src = strip(readFileSync(join(SRC, rel), 'utf8'))
      expect(src, `${rel}: the residue must follow </table>`).toMatch(/<\/table>[\s\S]{0,320}?<MoreRow/)
      // 🪤 CONTAINMENT, NOT PROXIMITY. The first draft asserted `<tbody>[\s\S]{0,600}?<MoreRow` did
      // not match, which fails on CORRECT code the moment the table body is short — the row is 40
      // characters past `</tbody>` and well inside 600. Same mistake as the "MoreRow within 24 lines"
      // pairing in #1618: a window is not a scope. Take the actual span between the tags.
      for (const body of src.match(/<tbody>[\s\S]*?<\/tbody>/g) ?? []) {
        expect(body, `${rel}: a div inside tbody is invalid markup`).not.toContain('<MoreRow')
      }
    }
  })

  it('a dashboard widget preview says it is one', () => {
    // 🔑 A SECOND GROUP, ON ITS OWN RULE. The census above only claims lists whose LABEL states a
    // total; a dashboard widget states nothing — its `Section` frame carries a bare label, so six of
    // twenty open tasks rendered with no count anywhere and read as all of them. These are the
    // opposite failure from the one above (no promise rather than an unmet one) and needed a separate
    // judgement, not an extension of the same sweep.
    //
    // 🪤 THE RULE IS NARROWER THAN "EVERY WIDGET": disclose a residue only when the hidden items are
    // real, persistent things the user could otherwise reach — open tasks, their own pins, the models
    // holding RAM on this machine, scheduled runs. All four have a destination that lists them.
    const widgets = join(SRC, 'pages/dashboard/widgets')
    const caps = cappedLists().filter((l) => l.rel.startsWith('pages/dashboard/widgets/'))
    expect(caps.length, 'the widget caps must be found').toBeGreaterThanOrEqual(5)
    const pins: Array<[string, RegExp]> = [
      ['pages/dashboard/widgets/TasksWidget.tsx', /<MoreRow total=\{visible\.length\} shown=\{6\} \/>/],
      ['pages/dashboard/widgets/ScheduleWidget.tsx', /<MoreRow total=\{visible\.length\} shown=\{6\} \/>/],
      ['pages/dashboard/widgets/PinnedArtifacts.tsx', /<MoreRow total=\{resolved\.length\} shown=\{6\} \/>/],
      ['pages/dashboard/widgets/OnThisMachine.tsx', /<MoreRow total=\{rows\.length\} shown=\{5\} \/>/],
    ]
    for (const [rel, re] of pins) {
      expect(strip(readFileSync(join(SRC, rel), 'utf8')), `${rel} must disclose its cap`).toMatch(re)
    }
    expect(readdirSync(widgets).length, 'the widget directory must be readable').toBeGreaterThan(4)
  })

  it('the SCHEDULE widget applied a standard it already held', () => {
    // Worth its own assertion because it is the strongest evidence the cap was a defect and not a
    // choice: this file already discloses the rows its archive fold hides, and its comment insists the
    // count come from the SERVER's full-window tally so "the label must not shrink to the fold" —
    // while a silent six-row truncation sat beside it. Both must stay.
    const src = strip(readFileSync(join(SRC, 'pages/dashboard/widgets/ScheduleWidget.tsx'), 'utf8'))
    expect(src, 'the fold disclosure it already had').toMatch(/\$\{scheduleSuppressed\} suppressed by a gate/)
    expect(src, 'and the cap disclosure it was missing').toMatch(/<MoreRow total=\{visible\.length\}/)
    expect(src, 'the residue is measured against what the fold shows').not.toMatch(
      /<MoreRow total=\{schedule\.length\}/,
    )
  })

  it('a GENERATED feed is deliberately left silent', () => {
    // 🪤 THE ONE WIDGET EXCLUDED, and the reason is the rule above rather than convenience.
    // `Suggestions` renders strings a backend generates from recent activity: there is no suggestions
    // page, nothing persists, and the list is regenerated — so "… 12 more" would name items the user
    // cannot reach and would recast a curated prompt list as a truncated inventory. Pinned so a later
    // pass does not "finish the set".
    const src = strip(readFileSync(join(SRC, 'pages/dashboard/widgets/Suggestions.tsx'), 'utf8'))
    expect(src, 'still capped').toMatch(/items\.slice\(0, 5\)/)
    expect(src, 'and still silent, on purpose').not.toMatch(/MoreRow/)
    expect(src, 'its own copy says it is a feed, which is why').toMatch(/they build from your activity/)
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
