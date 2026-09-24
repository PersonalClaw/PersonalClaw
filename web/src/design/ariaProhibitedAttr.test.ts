import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── `aria-label` on a role-less generic element is DISCARDED ─────────────────────────
//
// ARIA forbids naming a generic element: `<div aria-label="…">`, `<span>`, `<section>` and
// `<p>` map to no role (or to a role that prohibits a name), so the browser THROWS THE NAME
// AWAY. axe calls it `aria-prohibited-attr` (serious). The author's intent is silently lost —
// the markup looks labelled and announces nothing.
//
// Censused 9 sites in this tree, all one shape. The worst were the loading skeletons: seven
// `aria-busy="true" aria-label="Loading …"` regions whose label never reached a user, so a
// busy region announced nothing at all while data loaded.
//
// 🔑 THE CANONICAL FORM ALREADY EXISTED. `ChatPage`'s conversation skeleton has
// `role="status" aria-busy="true" aria-label="Loading conversation"` — correct, and the only
// one of the eight that was. So this is convergence onto a form the tree already ships, not
// a new invention: `role="status"` for a busy/loading region, `role="group"` for a labelled
// container of controls, `role="img"` for a graphic whose label is its only text.
//
// 🪤 WHY THIS HID FROM FIVE CYCLES OF axe RUNS. A loading skeleton exists only while a fetch
// is in flight. Measured on `settings/providers`: the violation is present at a 600ms settle
// and GONE by 1000ms. Cycle 50's gate waits ~1400ms, so it saw this as an intermittent
// failure and I attributed it to parallel-worker load. It is a real defect in a state that
// axe only catches if it happens to look early enough — which is exactly why a SOURCE rail
// is the right instrument for this family, not a DOM probe.

const SRC = join(process.cwd(), 'src')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

/** Opening tags for the generic elements that cannot carry a name, with file + line.
 *
 *  Scanning to the first `>` does NOT work on JSX — an attribute value like
 *  `onClick={() => f()}` contains one, so the tag would be truncated mid-attribute and a
 *  later `role=` missed (this produced five false positives in cycle 47). Track `{}` depth
 *  and only accept a `>` at depth 0. */
function genericTags(): Array<{ file: string; line: number; tag: string }> {
  const out: Array<{ file: string; line: number; tag: string }> = []
  for (const abs of walk(SRC)) {
    const text = readFileSync(abs, 'utf8')
    for (const m of text.matchAll(/<(div|span|section|p)\b/g)) {
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
        tag: text.slice(m.index!, end + 1),
      })
    }
  }
  return out
}

describe('a generic element never carries a discarded name', () => {
  const tags = genericTags()

  it('scans real JSX tags (not vacuously green)', () => {
    // A broken matcher would report zero offenders forever.
    expect(tags.length, 'the matcher must find the tree\'s generic tags').toBeGreaterThan(2000)
    // And it must actually see multi-line tags with braced attributes — the shape that
    // defeats a naive `[^>]*` scan.
    expect(
      tags.some((t) => t.tag.includes('\n') && t.tag.includes('{')),
      'the matcher must span multi-line tags with braced attribute values',
    ).toBe(true)
  })

  it('has no aria-label on a role-less div/span/section/p', () => {
    const offenders = tags
      .filter((t) => /\saria-label=/.test(t.tag) && !/\srole=/.test(t.tag))
      // aria-hidden removes the element from the a11y tree entirely, so there is no name
      // to discard and nothing for a user to miss.
      .filter((t) => !/\saria-hidden/.test(t.tag))
      .map((t) => `${t.file}:${t.line}`)
    expect(
      offenders,
      'ARIA prohibits naming a generic element — the browser DISCARDS these labels and axe\n' +
        'reports aria-prohibited-attr (serious). Give the element the role that matches what\n' +
        'it is: `status` for a busy/loading region, `group` for a labelled set of controls,\n' +
        '`img` for a graphic whose label is its only text.\n  ' + offenders.join('\n  '),
    ).toEqual([])
  })

  it('every aria-busy region is a status region', () => {
    // A busy region with a discarded name announces NOTHING while it loads, which is the
    // whole point of marking it busy. `role="status"` is the form ChatPage already shipped.
    const unnamed = tags
      .filter((t) => /\saria-busy=/.test(t.tag) && /\saria-label=/.test(t.tag))
      .filter((t) => !/role="status"/.test(t.tag))
      .map((t) => `${t.file}:${t.line}`)
    expect(
      unnamed,
      'A labelled aria-busy region needs role="status" or its label is discarded:\n  ' +
        unnamed.join('\n  '),
    ).toEqual([])
  })
})
