import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A file picker nobody can reach without a mouse ────────────────────────────────────────────
//
// `#/knowledge/new` is a CREATE form, and with a file type selected its only file-choosing affordance
// was a drop area: a bare `div` with an `onClick` that forwarded to `<input type="file" hidden>`.
// `hidden` removes the input from the tab order and a `div` with no role is not a control, so the
// step could not be completed at all without a pointer. Measured live at 1440×900:
//
//   drop area   `role` null · `tabindex` null        input   `hidden` true · no accessible name
//   keyboard    **0 of 55** Tab presses reached either one
//
// WCAG 2.1.1. axe reports nothing, because there is no element for it to fault — the control simply
// is not there.
//
// 🔑 THE FIX IS THE REAL INPUT, NOT A NEW BUTTON. `sr-only` instead of `hidden` keeps the native
// control: Tab reaches it and Space/Enter opens the system picker, which no hand-rolled button can
// reproduce. This app already had that shape in one place — `pages/workflows/OutboxPanel.tsx`, whose
// comment says it outright ("The real file picker below is the control; this button only forwards to
// it") — so this converged onto an existing form rather than inventing one.
//
// 🪤 THE INPUT HAS TO BE INSIDE THE THING THAT DRAWS THE RING. The first version left it as a
// SIBLING of the drop area and used `has-[input:focus-visible]:ring-*`; the ring computed to `none`
// in both themes while the input was genuinely `:focus-visible`, because `:has()` only looks at
// descendants. Tailwind's `peer` would reach a sibling, but nothing in this codebase uses `peer`,
// while `has-[…]` is the idiom every row and tab strip here already uses — so the input moved inside.
//
// 🪤 AND MOVING IT IN CREATED A RE-ENTRY BUG THAT ONLY A COUNTER FINDS. Activating the input
// dispatches a click that bubbles to the drop area's `onClick`, which calls `.click()` again — a
// second file dialog on every keyboard open. Counted with Playwright's `filechooser` event: with the
// `e.target === fileRef.current` guard it is exactly **1** per Enter, in both themes.
//
// ── The family, censused ──────────────────────────────────────────────────────────────────────
//
// Every `<input type="file">` in the tree, and what can reach it:
//
//   workflows/OutboxPanel       `sr-only` + a forwarding QuietButton   ← the canonical form
//   knowledge/KnowledgeCreate   `sr-only` inside the drop area          ← fixed this cycle
//   loop/LoopComposer           `sr-only` inside its `<label>`          ← fixed this cycle
//   loops/DesignCockpitPage     `hidden`, real Button "Upload screenshot"
//   settings/PortabilityPanel   `hidden`, real Button
//   files/browse/FileTree       `hidden`, ContextMenu item "Upload here"
//   ui/Composer                 `hidden`, PlusMenu item
//
// 🔑 A `hidden` input is FINE when a real button forwards to it — the button is the control and it is
// keyboard-operable. What is never fine is a `hidden` input whose only trigger is a click-only `div`
// or a bare `<label>`: a label is not focusable, so `#/loops`' "Attach reference" was pointer-only in
// exactly the same way the knowledge drop area was, and it is fixed in the same change.
//
// 🪤 `PortabilityPanel`'s hidden input carries an `aria-label`, which is inert — a hidden element is
// not in the accessibility tree. Harmless (its Button is the control), recorded so it is not read as
// evidence that the input is reachable.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

/** Brace-aware tag text: the `>` inside `onChange={(e) => …}` does not end the tag. */
function tags(src: string, name: string): string[] {
  const out: string[] = []
  const re = new RegExp(`<${name}(?=[\\s>])`, 'g')
  let m: RegExpExecArray | null
  while ((m = re.exec(src))) {
    let i = m.index + 1 + name.length, depth = 0, quote: string | null = null
    for (; i < src.length; i++) {
      const c = src[i]
      if (quote) { if (c === quote) quote = null; continue }
      if (c === '"' || c === "'" || c === '`') { quote = c; continue }
      if (c === '{') depth++
      else if (c === '}') depth--
      else if (c === '>' && depth === 0) break
    }
    out.push(src.slice(m.index, i + 1))
  }
  return out
}

const pickers = () => walk(SRC).flatMap((abs) => {
  const src = readFileSync(abs, 'utf8')
  return tags(src, 'input')
    .filter((t) => /type="file"/.test(t))
    .map((tag) => ({ file: abs.slice(SRC.length + 1), tag, src }))
})

/** A `hidden` picker is reachable only through a real control that forwards to it. Each entry names
 *  the control, so "it has a button somewhere" can never be assumed. */
const FORWARDED: Record<string, RegExp> = {
  'pages/loops/DesignCockpitPage.tsx': /<Button[^>]*onClick=\{\(\) => fileRef\.current\?\.click\(\)\}/,
  'pages/settings/PortabilityPanel.tsx': /<Button[^>]*onClick=\{\(\) => fileRef\.current\?\.click\(\)\}/,
  'pages/files/browse/FileTree.tsx': /label: 'Upload here', onClick: \(\) => uploadInput\.current\?\.click\(\)/,
  'ui/Composer.tsx': /onAttach=\{\(\) => fileRef\.current\?\.click\(\)\}/,
}

describe('every file picker can be reached without a mouse', () => {
  it('finds the population (not vacuously green)', () => {
    expect(pickers().length, 'the file-input census must not go empty').toBeGreaterThanOrEqual(7)
  })

  it('is either focusable itself, or forwarded to by a named real control', () => {
    const bad: string[] = []
    for (const { file, tag, src } of pickers()) {
      const focusable = /className="sr-only"|className={`sr-only/.test(tag) || /className="sr-only"/.test(tag)
      if (focusable) continue
      const fwd = FORWARDED[file]
      if (fwd && fwd.test(src)) continue
      bad.push(`${file}: hidden picker with no focusable input and no named forwarding control`)
    }
    expect(bad, `a file picker is pointer-only:\n${bad.join('\n')}`).toEqual([])
  })

  it('the two fixed this cycle keep their focusable input AND a visible focus ring', () => {
    const cases: [string, RegExp][] = [
      ['pages/knowledge/KnowledgeCreatePage.tsx', /border-dashed[\s\S]{0,200}?has-\[input:focus-visible\]:ring-2/],
      ['pages/loop/LoopComposer.tsx', /<label[\s\S]{0,300}?has-\[input:focus-visible\]:ring-2/],
    ]
    for (const [rel, ring] of cases) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      expect(code, `${rel}: the picker must stay focusable`).toMatch(/type="file"[\s\S]{0,220}?className="sr-only"/)
      expect(code, `${rel}: a visually hidden input needs the ring drawn on its container`).toMatch(ring)
      // …and the ring only works if the input is a DESCENDANT, which is what the sibling version got
      // wrong. Asserted as ORDER: the container's class list comes before the input in the same block.
      expect(code, `${rel}: the input must sit inside the element carrying the ring`)
        .toMatch(/has-\[input:focus-visible\]:ring-primary\b[\s\S]{0,400}?type="file"/)
    }
  })

  it('the knowledge drop area guards the re-entrant click', () => {
    // Without this, activating the input by keyboard bubbles into the drop area's own onClick and asks
    // for a SECOND file dialog. Counted live: exactly 1 chooser per Enter with the guard.
    const code = readFileSync(join(SRC, 'pages/knowledge/KnowledgeCreatePage.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code).toMatch(/if \(e\.target === fileRef\.current\) return/)
  })

  it('the knowledge copy no longer says only "click"', () => {
    // The visible affordance text is the instruction; with a keyboard route it must not describe one
    // input device.
    const src = readFileSync(join(SRC, 'pages/knowledge/KnowledgeCreatePage.tsx'), 'utf8')
    expect(src).not.toMatch(/or click to choose/)
    expect(src).toMatch(/or choose one/)
  })
})
