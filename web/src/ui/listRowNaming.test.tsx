import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ListRow } from './ListScaffold'

// ── A clickable row must be named after the ENTITY, not after its whole subtree ──
//
// `ListRow` renders `role="button" tabIndex={0}` on the wrapper. With no `aria-label`,
// the accessible name is computed from the subtree — so AT announces the entire card as
// one button name. Measured on the live DOM across 170 rows on 7 surfaces BEFORE this
// change (avg / max characters of the computed name):
//
//     inbox     318 / 2001      knowledge 685 / 946       skills 333 / 560
//     prompts   161 /  236      agents    104 /  142       triggers 86 / 109
//
// A 2001-character button name is not a name; it is the row's content announced where its
// identity belongs. After threading `label` through all 11 interactive call sites:
//
//     inbox      10 /   16      knowledge  76 /  118      skills  13 /  19
//     prompts    19 /   30      agents     19 /   25       triggers 41 /  52
//
// AT now reads `button "FX rate source of record"` with the summary still available
// underneath as ordinary text, and the row's own controls still individually named and
// keyboard reachable (Tab: checkbox → tag filter → next row — verified live).
//
// 🔑 THIS RAIL CHECKS THE CALL SITES, NOT JUST THE PRIMITIVE. A test that only rendered
// `<ListRow label="x">` would pass forever while a new page shipped an unnamed row — the
// exact gap recorded as "a test can exercise a mechanism, not its use". So the second
// block greps every interactive `<ListRow` in the tree and requires a `label` prop.
//
// NOT in scope: `nested-interactive` (26 nodes on knowledge, 34 on workflows) is a
// SEPARATE defect on the same component — rows that contain their own controls. Naming
// the row does not remove the nesting, and it should not: those controls are reachable
// and individually named today, so it is a lower-urgency structural question about the
// row-as-button pattern. Fixing it means re-homing the hit target for 11 callers, and it
// gets its own PR rather than riding along here.

const SRC = join(process.cwd(), 'src')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

/** Every `<ListRow …>` opening tag in the tree, with file + line + its full attributes.
 *
 *  ⚠️ Scanning to the first `>` DOES NOT WORK here and produced five false positives while
 *  this rail was being written: an attribute value like `onClick={() => setOpenId(it.id)}`
 *  contains a `>`, so a `[^>]*` match truncates the tag mid-attribute and misses the
 *  `label` that follows. The tag end has to be found by tracking `{}` depth and only
 *  accepting a `>` at depth 0. */
function listRowTags(): Array<{ file: string; line: number; attrs: string }> {
  const out: Array<{ file: string; line: number; attrs: string }> = []
  for (const abs of walk(SRC)) {
    const text = readFileSync(abs, 'utf8')
    for (const m of text.matchAll(/<ListRow\b/g)) {
      let depth = 0
      let end = -1
      for (let i = m.index! + m[0].length; i < text.length; i++) {
        const ch = text[i]
        if (ch === '{') depth++
        else if (ch === '}') depth--
        else if (ch === '>' && depth === 0) { end = i; break }
      }
      if (end === -1) continue
      out.push({
        file: abs.slice(SRC.length + 1),
        line: text.slice(0, m.index).split('\n').length,
        attrs: text.slice(m.index! + m[0].length, end),
      })
    }
  }
  return out
}

describe('ListRow names the row, not its contents', () => {
  it('puts `label` on the button role', () => {
    render(<ListRow onClick={() => {}} label="Deploy pipeline"><span>lots of body text</span></ListRow>)
    const row = screen.getByRole('button', { name: 'Deploy pipeline' })
    // The name must be the label EXACTLY — not label + subtree.
    expect(row.getAttribute('aria-label')).toBe('Deploy pipeline')
  })

  it('does not name a NON-interactive row (there is no button to name)', () => {
    const { container } = render(<ListRow label="unused"><span>static content</span></ListRow>)
    const div = container.querySelector('[aria-label]')
    expect(div, 'a row with no onClick is not a button and must not claim a name').toBeNull()
  })

  it('still exposes the row content to sighted readers', () => {
    // The fix must not have hidden the body — only stopped it being read AS the name.
    render(<ListRow onClick={() => {}} label="Short name"><span>the visible summary</span></ListRow>)
    expect(screen.getByText('the visible summary')).toBeTruthy()
  })
})

describe('every interactive ListRow call site is named', () => {
  const tags = listRowTags()

  it('scans real call sites (not vacuously green)', () => {
    // A broken matcher would report zero unnamed rows forever.
    expect(tags.length, 'the matcher must find the tree\'s <ListRow> tags').toBeGreaterThan(10)
    expect(
      tags.filter((t) => /onClick/.test(t.attrs)).length,
      'and most of them are interactive',
    ).toBeGreaterThan(8)
  })

  it('has no clickable row without a label', () => {
    const unnamed = tags
      .filter((t) => /onClick/.test(t.attrs) && !/\blabel=/.test(t.attrs))
      .map((t) => `${t.file}:${t.line}`)
    expect(
      unnamed,
      'A clickable ListRow with no `label` takes its accessible name from its whole subtree —\n' +
        'measured up to 2001 characters for one inbox row. Pass the entity title:\n  ' +
        unnamed.join('\n  '),
    ).toEqual([])
  })
})
