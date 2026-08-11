import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── The control-name floor, held where it was MEASURED ───────────────────────────────
//
// Cycle 54 censused the raw-control population the `primitiveAdoption` ratchet tracks
// (`rawInput: 149`) and found the a11y half of it CLEAN. That negative result is worth a
// rail, because the next pass would otherwise re-derive it from the same misleading source
// signal:
//
//   141 raw <input>/<textarea>/<select> outside ui/, across 50 files
//    37 of them with NO aria-label / aria-labelledby / id / placeholder IN SOURCE
//     0 genuinely unnamed on the LIVE DOM
//
// The 37 are named by a WRAPPING <label>, which no source scan can see. Measured across 23
// routes + 3 opened modals + 7 create routes (and `knowledge/new` per kind, since it is a
// type picker before it is a form): **0 unnamed controls**. axe agrees — no
// `label`/`aria-input-field-name` violation anywhere.
//
// 🔑 SO THIS RAIL DOES NOT COUNT RAW INPUTS. Their number is the `primitiveAdoption`
// ratchet's job and says nothing about accessibility. What it pins instead is the ONE
// construct that legitimately has no accessible name, so that a genuinely unnamed control
// cannot hide behind the same shape.
//
// THE HIDDEN FILE INPUT. All 6 in the tree are `hidden` / `className="hidden"` — clicked
// programmatically by a visible button, never focused. Measured: `display: none`,
// `offsetParent: null`, `e.focus()` does not move focus, and axe reports nothing. They are
// the two "unnamed" hits the DOM probe returned, and both are FALSE POSITIVES.
//
// A file input that is NOT hidden is a different thing: it is a real, focusable control that
// a user can reach, and it needs a name like any other. That is what this rail catches.

const SRC = join(process.cwd(), 'src')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

/** Complete opening tags for `<input>`, tracking {} depth.
 *
 *  Never scan to the first `>` and never use a lookahead across the tag: an attribute value
 *  like `onChange={(e) => f(e)}` contains a `>`, which truncates the match and drops any
 *  later attribute. That mistake has now produced wrong counts three times in this session
 *  (cycles 47, 51, 53) — collect the whole tag, then test its text. */
function inputTags(): Array<{ file: string; line: number; tag: string }> {
  const out: Array<{ file: string; line: number; tag: string }> = []
  for (const abs of walk(SRC)) {
    const text = readFileSync(abs, 'utf8')
    for (const m of text.matchAll(/<input\b/g)) {
      let depth = 0
      for (let i = m.index! + m[0].length; i < text.length; i++) {
        const ch = text[i]
        if (ch === '{') depth++
        else if (ch === '}') depth--
        else if (ch === '>' && depth === 0) {
          out.push({
            file: abs.slice(SRC.length + 1),
            line: text.slice(0, m.index).split('\n').length,
            tag: text.slice(m.index!, i + 1),
          })
          break
        }
      }
    }
  }
  return out
}

const tags = inputTags()
const fileInputs = tags.filter((t) => /type=["']file["']/.test(t.tag))

describe('the file-input escape hatch stays an escape hatch', () => {
  it('scans real <input> tags (not vacuously green)', () => {
    expect(tags.length, 'the matcher must find the tree\'s <input> tags').toBeGreaterThan(100)
    expect(fileInputs.length, 'and the file inputs among them').toBeGreaterThanOrEqual(6)
    // The matcher must span a multi-line tag with a braced handler — the shape a naive
    // `[^>]*` scan truncates.
    expect(
      tags.some((t) => t.tag.includes('\n') && t.tag.includes('=>')),
      'the matcher must span multi-line tags with arrow-function attributes',
    ).toBe(true)
  })

  it('every file input is hidden, so it needs no name', () => {
    // A VISIBLE file input is a focusable control a user can reach: it needs an accessible
    // name like any other. A hidden one is triggered by a labelled button and is out of the
    // a11y tree entirely (measured: display none, offsetParent null, unfocusable).
    const visible = fileInputs
      .filter((t) => !/\bhidden\b/.test(t.tag) && !/className=["'][^"']*\bhidden\b/.test(t.tag) && !/sr-only/.test(t.tag))
      .filter((t) => !/aria-label|aria-labelledby/.test(t.tag))
      .map((t) => `${t.file}:${t.line}`)
    expect(
      visible,
      'A file input that is NOT hidden is reachable and must carry a name (or be hidden and\n' +
        'driven by a labelled button, which is the pattern the other 6 use):\n  ' + visible.join('\n  '),
    ).toEqual([])
  })
})

describe('an on-demand edit input carries its own name', () => {
  // THE ONE REAL DEFECT CLASS this census found. Six inline rename/edit inputs replace a
  // visible title when a user starts editing — `autoFocus` with no aria-label, no
  // placeholder, no id, and no wrapping <label>. They appear ON DEMAND, so no scan of a
  // resting page ever sees them, and a screen-reader user lands on an unnamed textbox where
  // a titled row used to be:
  //
  //   ChatPage (session)  ·  ProjectsSection (project)  ·  FileTree (file/folder)
  //   LoopCockpitPage (loop)  ·  LoopPlanReview (plan title)  ·  TerminalPage (tab)
  //
  // `autoFocus` is the reliable marker: an input that steals focus the moment it mounts IS
  // the on-demand edit pattern. It is also exactly why the name matters — focus moves there
  // with no user action, so the announcement is the only thing telling them where they are.
  const autoFocused = tags.filter((t) => /\bautoFocus\b/.test(t.tag))

  it('finds the on-demand inputs (not vacuously green)', () => {
    expect(autoFocused.length, 'expected the inline rename/edit inputs').toBeGreaterThanOrEqual(6)
  })

  it('every autoFocus input is named', () => {
    const nameless = autoFocused
      .filter((t) => !/aria-label|aria-labelledby|\bid=|placeholder=/.test(t.tag))
      .map((t) => `${t.file}:${t.line}`)
    expect(
      nameless,
      'An autoFocus input takes focus with no user action, so its name is the ONLY thing\n' +
        'telling a screen-reader user where they landed. These have none:\n  ' + nameless.join('\n  '),
    ).toEqual([])
  })
})

// ── NOT asserted here, deliberately ──────────────────────────────────────────────────
// A blanket "every raw input has a name source" rail was WRITTEN FIRST and REMOVED: it
// flagged 18 sites, and the DOM said every rendered one of them was named. Most are wrapped
// in a `<label>` (invisible to a source scan) and the rest — `type=range`, `type=time`,
// `type=number` inside a `Field` — did not render in the validation fixture's state, so the
// rail asserted a defect nobody could observe.
//
// Measured floor, for the record: **0 unnamed controls** across 23 routes + 3 opened modals
// + 7 create routes (`knowledge/new` driven per kind, since it is a type picker before it is
// a form), with axe reporting no `label` / `aria-input-field-name` violation anywhere. The
// raw-input COUNT is the `primitiveAdoption` ratchet's business and says nothing about
// accessibility — 141 raw inputs, 0 a11y defects among the ones that render.
