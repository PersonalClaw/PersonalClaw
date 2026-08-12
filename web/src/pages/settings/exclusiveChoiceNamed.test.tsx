import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { SegToggle } from './bento'

// ── An exclusive-choice pill group that says neither what it sets nor which one is on ──────
//
// Measured on the live DOM (1440×900, dark) before this change — four groups, eleven options:
//
//   #/settings          Mode           Light / Dark / Auto          name "Light"        pressed null
//   #/settings          Density        Comfortable / Compact        name "Comfortable"  pressed null
//   #/settings          Min severity   All / Warn+ / Errors         name "All"          pressed null
//   #/settings/design   (unlabelled)   Dark / Light / Auto          name "Dark"         pressed null
//
//   4/4 groups with no accessible name · 11/11 options with no pressed or selected state
//
// Two distinct losses. The DIMENSION is missing, so the options announce bare values — and on
// `#/settings/design` the group sits under a heading that reads "Color scheme", which is not what
// it sets. And the SELECTED state lives only in an inline `background`, so nothing but sighted
// pixel comparison tells you which mode, density or severity is active (WCAG 4.1.2, level A —
// a toggle's state must be programmatically determinable).
//
// 🪤 WHY NO TOOL HERE REPORTED IT. `ux-audit`'s label check asks whether a control HAS a name;
// each of these has one — its own text content. axe agrees, because a plain `<button>` is not
// expected to carry a pressed state, so its absence is not a violation to find. A name that
// exists but does not say what it controls is invisible to both.
//
// The canonical form was already shipped three times over: `WidthPill` announces
// `Content width: <preset>` + `aria-pressed`, `HeaderModePill` composes `<dimension>: <value>`,
// and the composer pills do the same with a required `dimension` prop. `SegToggle` now takes a
// REQUIRED `ariaLabel` for exactly that reason — typecheck stops an unnamed new call site
// before this rail has to.

const SETTINGS = join(process.cwd(), 'src/pages/settings')
const SRC = join(process.cwd(), 'src')

/** Source with comments removed. Two rails in this session went red on their own explanation:
 *  a matcher that scans raw source will happily match the defect quoted in the comment above
 *  it. Strip first, then match. */
const stripComments = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

/** Complete `<Tag …>` openings, tracking `{}` depth. A `[^>]*>` matcher stops at the `>` inside
 *  `onPick={(v) => save(v)}` and reports every site as attribute-less — the mistake that has
 *  produced wrong counts repeatedly in this tree. */
function tags(src: string, name: string): string[] {
  const out: string[] = []
  for (const m of src.matchAll(new RegExp(`<${name}\\b`, 'g'))) {
    let depth = 0
    for (let i = m.index! + m[0].length; i < src.length; i++) {
      const ch = src[i]
      if (ch === '{') depth++
      else if (ch === '}') depth--
      else if (ch === '>' && depth === 0) { out.push(src.slice(m.index!, i + 1)); break }
    }
  }
  return out
}

describe('SegToggle announces its dimension and its state', () => {
  it('names each option <dimension>: <value>', () => {
    render(<SegToggle ariaLabel="Mode" value="dark" onPick={vi.fn()}
      options={[{ key: 'light', label: 'Light' }, { key: 'dark', label: 'Dark' }, { key: 'auto', label: 'Auto' }]} />)
    expect(screen.getByRole('button', { name: 'Mode: Light' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Mode: Dark' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Mode: Auto' })).toBeTruthy()
  })

  it('marks exactly the active option as pressed', () => {
    render(<SegToggle ariaLabel="Density" value="less" onPick={vi.fn()}
      options={[{ key: 'more', label: 'Comfortable' }, { key: 'less', label: 'Compact' }]} />)
    expect(screen.getByRole('button', { name: 'Density: Compact' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'Density: Comfortable' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('leaves the VISIBLE label alone — this is a naming fix, not a redesign', () => {
    const { container } = render(<SegToggle ariaLabel="Min severity" value="info" onPick={vi.fn()}
      options={[{ key: 'info', label: 'All' }, { key: 'error', label: 'Errors' }]} />)
    expect([...container.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['All', 'Errors'])
  })
})

describe('every SegToggle call site names its dimension', () => {
  const sites = walk(SRC).flatMap((f) => tags(stripComments(readFileSync(f, 'utf8')), 'SegToggle').map((t) => ({ f, t })))

  it('finds the call sites (not vacuously green)', () => {
    // Three at the time of writing. `ariaLabel` is a required prop, so typecheck is the real
    // gate; this floor only stops the assertion below from passing by matching nothing.
    expect(sites.length, 'the matcher must find the SegToggle call sites').toBeGreaterThanOrEqual(3)
  })

  it('has no unnamed call site', () => {
    const mute = sites.filter((s) => !/\bariaLabel=/.test(s.t))
    expect(mute.map((s) => s.f), 'SegToggle without a dimension').toEqual([])
  })
})

describe("the Design panel's hand-rolled mode pills agree with the primitive", () => {
  const src = stripComments(readFileSync(join(SETTINGS, 'DesignPanel.tsx'), 'utf8'))

  it('announces Mode: <value> and its pressed state', () => {
    // Source-level: the panel mounts the whole appearance store (server-backed theme list), so a
    // render test here would assert about a fetch, not about a name.
    expect(src).toMatch(/aria-label=\{`Mode: \$\{m\.label\}`\}/)
    expect(src).toMatch(/aria-pressed=\{on\}/)
  })

  it('never uses the saved-scheme word for the light/dark axis', () => {
    // The word is legitimately busy on this page: schemes persist to /api/themes, so "Save theme",
    // "Delete saved theme", "Name the theme first" and "Loading saved themes…" all mean a stored
    // color identity, and they stay. What it must NOT also mean is light/dark — the two senses were
    // 200px apart, the prose describing the light/dark one while the buttons below saved and
    // deleted the other kind.
    //
    // An allowlist of sanctioned phrasings was the first attempt and it flagged an import path and
    // a legitimate button reason, so this asserts the defect directly: the word may never be
    // PAIRED with a light/dark word. The `<\/strong>` arm is the original shape — the mode was
    // interpolated inside a `<strong>`, which no string-literal scan can see.
    const paired = [/\b(system|light|dark)\b[^\n]{0,20}\btheme\b/i, /<\/strong>\s*theme\b/i, /\btheme\b[^\n]{0,20}\b(you are|currently|system)\b/i]
      .filter((re) => re.test(src)).map((re) => String(re))
    expect(paired, 'a user-visible string pairs the saved-scheme word with the light/dark axis').toEqual([])

    const mentions = (src.match(/\btheme(s)?\b/gi) || []).length
    expect(mentions, 'the saved-scheme copy must still be there (else this passes vacuously)').toBeGreaterThanOrEqual(8)
  })

  it('spells color the way the other 1600 sites do', () => {
    expect(src).not.toMatch(/colour/i)
  })
})
