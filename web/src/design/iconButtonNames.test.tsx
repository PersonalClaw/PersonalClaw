import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ChipInput } from '../ui/forms'

// ── Every icon-only button must carry its own name ──────────────────────────────
//
// A `<button>` whose entire body is one self-closing element renders no text, so it is announced as
// bare "button". This is the ONE slice of the accessible-name family that a source rail can decide:
// nothing an ancestor does can name a button, unlike an `<input>` (which a wrapping `<label>` or a
// publishing `Field` can name invisibly — a source rail for THAT reported 8 offenders against the
// DOM's 0, so it was deliberately not shipped).
//
// Tree-wide census: 63 icon-only buttons, 12 without an `aria-label`. Those 12 split in two, and the
// split is the whole judgement:
//
//   9 carry `title=`  → NOT a defect. Verified against Chromium's own accessibility tree over CDP
//                       (`Accessibility.getFullAXTree`), because reading attributes back proves
//                       nothing: `<button title="Delete">` computes `name="Delete"` from
//                       `source=attribute`, and `aria-label` correctly SUPERSEDES `title` when both
//                       are present. A tooltip is a weaker affordance (no touch, no keyboard) but it
//                       IS a name, so converging them would be churn, not a fix.
//   3 carry nothing   → real defects, all fixed here.
//
// The three, and why each name is shaped as it is:
//
//   ui/forms.tsx ChipInput   `Remove ${v}` — ONE PER CHIP, so a constant would announce N identical
//                            destructive buttons. Fixing the PRIMITIVE reaches all 8 consumers.
//   KnowledgeListPage        `Close the intent editor` — a singleton, so a constant IS correct.
//   TaskDetail               `Post comment` — likewise one per composer.
//
// Measured on the live DOM after: 138 icon-only buttons across 8 routes, ALL named. Driving two chips
// into a real form gave `["Remove alpha","Remove beta"]` — distinct, which is the point.

const SRC = join(process.cwd(), 'src')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

interface IconButton { line: number; tag: string; body: string; named: boolean; titled: boolean }

/** Every `<button>` whose body is exactly one self-closing element. */
function iconOnlyButtons(src: string): IconButton[] {
  const out: IconButton[] = []
  const re = /<button\b/g
  let m: RegExpExecArray | null
  while ((m = re.exec(src)) !== null) {
    // Finding the tag's end is NOT `[^>]*>`: that stops at the `>` inside `onClick={() => f()}`.
    // Track brace depth. (That bug made an earlier version of this scanner match NOTHING.)
    let depth = 0
    let end = -1
    for (let i = m.index + 1; i < src.length; i++) {
      const ch = src[i]
      if (ch === '{') depth++
      else if (ch === '}') depth--
      else if (depth === 0 && ch === '>') { end = i + 1; break }
    }
    if (end === -1) continue
    const close = src.indexOf('</button>', end)
    if (close === -1) continue
    const tag = src.slice(m.index, end)
    const body = src.slice(end, close).trim()
    // `\w*` not `\w+`: the icon is often a SINGLE letter (`<X size={12} />`), and `\w+` demands a
    // second word character — the other half of why the earlier scanner silently matched nothing.
    if (!/^<[A-Z]\w*[^>]*\/>$/.test(body)) continue
    out.push({
      line: src.slice(0, m.index).split('\n').length,
      tag,
      body,
      named: /aria-label|aria-labelledby/.test(tag),
      titled: /\btitle=/.test(tag),
    })
  }
  return out
}

describe('the ChipInput remove button names the chip it removes', () => {
  it('two chips get two DIFFERENT names', () => {
    // One button per chip: a constant label would be non-null and still announce identically, on a
    // destructive action. `values` is the discriminator.
    const { container } = render(<ChipInput values={['alpha', 'beta']} onChange={() => {}} />)
    const names = [...container.querySelectorAll('button')].map((b) => b.getAttribute('aria-label'))
    expect(names).toEqual(['Remove alpha', 'Remove beta'])
    expect(new Set(names).size).toBe(names.length)
  })

  it('a chip with no remove name would be announced as bare "button"', () => {
    // Pins the mechanism at the source, so a refactor that drops the attribute reds here too.
    expect(strip(readFileSync(join(SRC, 'ui/forms.tsx'), 'utf8')))
      .toMatch(/aria-label=\{`Remove \$\{v\}`\}/)
  })
})

describe('the two singleton icon buttons', () => {
  it('the intent editor close button is named', () => {
    expect(strip(readFileSync(join(SRC, 'pages/knowledge/KnowledgeListPage.tsx'), 'utf8')))
      .toMatch(/aria-label="Close the intent editor"/)
  })

  it('the task comment send button is named', () => {
    expect(strip(readFileSync(join(SRC, 'pages/tasks/TaskDetail.tsx'), 'utf8')))
      .toMatch(/aria-label="Post comment"/)
  })
})

describe('the rail: no icon-only button ships without a name', () => {
  const scanned = walk(SRC).map((abs) => ({
    rel: abs.slice(SRC.length + 1),
    buttons: iconOnlyButtons(strip(readFileSync(abs, 'utf8'))),
  }))

  it('every icon-only button has an aria-label or a title', () => {
    // `title=` is ACCEPTED, not overlooked: Chromium computes an accessible name from it (verified
    // over CDP). It is a weaker affordance than aria-label — no touch, no keyboard — but converging
    // the 9 existing ones would be churn, and this rail's job is to stop the NAMELESS shape.
    const offenders = scanned.flatMap(({ rel, buttons }) =>
      buttons.filter((b) => !b.named && !b.titled)
        .map((b) => `${rel}:${b.line}  ${b.body}`))
    expect(
      offenders,
      `An icon-only button with neither aria-label nor title is announced as just "button":\n  ` +
        offenders.join('\n  '),
    ).toEqual([])
  })

  it('the rail is not vacuously green — it finds the buttons it guards', () => {
    // Two regex bugs in the previous cycle each made this scanner match ZERO buttons while reporting
    // a clean sweep. `expect(offenders).toEqual([])` cannot tell "nothing is broken" from "my matcher
    // is broken"; only a positive match can.
    const all = scanned.flatMap((s) => s.buttons)
    expect(all.length, 'the scanner must find the tree\'s icon-only buttons').toBeGreaterThan(40)

    // It must see the three fixed here, as NAMED.
    const named = (rel: string, needle: RegExp) =>
      scanned.find((s) => s.rel === rel)?.buttons.some((b) => b.named && needle.test(b.tag)) ?? false
    expect(named('ui/forms.tsx', /Remove \$\{v\}/)).toBe(true)
    expect(named('pages/knowledge/KnowledgeListPage.tsx', /Close the intent editor/)).toBe(true)
    expect(named('pages/tasks/TaskDetail.tsx', /Post comment/)).toBe(true)

    // And it must still FLAG the nameless shape, including across an inline arrow handler and with a
    // single-letter icon — the exact two shapes that defeated the earlier version.
    const sample = `<button type="button" onClick={() => f()}><X size={12} /></button>`
    const s2 = iconOnlyButtons(sample)
    expect(s2.length).toBe(1)
    expect(s2[0].named).toBe(false)
    expect(s2[0].titled).toBe(false)
  })

  it('titled-but-unlabelled buttons are a known, counted population', () => {
    // If this count moves, someone either added a title-only button (fine, but notice it) or
    // converted one — either way it should be a deliberate diff, not a silent drift.
    const titledOnly = scanned.flatMap(({ rel, buttons }) =>
      buttons.filter((b) => !b.named && b.titled).map((b) => `${rel}:${b.line}`))
    expect(titledOnly.length, `title-only icon buttons:\n  ${titledOnly.join('\n  ')}`).toBe(9)
  })
})
