import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { SegToggle } from './bento'
import { SegPills } from './settingsUI'

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
//
// ── AND THEN THIS RAIL MISSED THE BIGGEST MEMBER, because it enumerated a COMPONENT, not a FAMILY.
//
// `SegPills` (pages/settings/settingsUI.tsx) is the same idiom with 8 call sites across 6 settings
// panels, and it had neither half. Re-measured on the live DOM at 1440×900 dark across
// `#/settings/chat`, `/guardrails`, `/notifications`, `/agent`:
//
//   BEFORE   34 groups · 126 options · 0 with the dimension in any name · 0 with any pressed state
//   AFTER    34 groups · 126 options · 0 unnamed · 0 stateless (exactly one pressed per group)
//
// 🔑 The notification rules matrix renders **26 of those groups at once**, every one of them
// `[Never | Badge | Notify | Digest]`. A screen-reader user heard 26 indistinguishable sets of four
// bare buttons — no rule name, no live mode. That is the single worst instance of this defect in the
// app, and a rail scoped to `SegToggle` could never see it.
//
// So the last describe DERIVES the family: any component that maps an `options` list to buttons and
// compares one of them to a `value` must mark its state programmatically. That is the check that
// would have found `SegPills` on the day `SegToggle` was fixed.

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

describe('SegPills announces its dimension and its state', () => {
  it('names each option <dimension>: <value>', () => {
    render(<SegPills ariaLabel="Widget density" value="more" onChange={vi.fn()}
      options={[{ key: 'more', label: 'More' }, { key: 'less', label: 'Less' }]} />)
    expect(screen.getByRole('button', { name: 'Widget density: More' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Widget density: Less' })).toBeTruthy()
  })

  it('marks exactly the active option as pressed', () => {
    render(<SegPills ariaLabel="Scan mode" value="redact" onChange={vi.fn()}
      options={[{ key: 'warn', label: 'Warn' }, { key: 'redact', label: 'Redact' }, { key: 'block', label: 'Block' }]} />)
    expect(screen.getByRole('button', { name: 'Scan mode: Redact' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'Scan mode: Warn' }).getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByRole('button', { name: 'Scan mode: Block' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('leaves the VISIBLE label alone — a naming fix, not a redesign', () => {
    const { container } = render(<SegPills ariaLabel="Restore window" value="30" onChange={vi.fn()}
      options={[{ key: '15', label: '15 min' }, { key: '30', label: '30 min' }]} />)
    expect([...container.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['15 min', '30 min'])
  })
})

describe('every SegPills call site names its dimension', () => {
  const sites = walk(SRC).flatMap((f) => tags(stripComments(readFileSync(f, 'utf8')), 'SegPills').map((t) => ({ f, t })))

  it('finds the call sites (not vacuously green)', () => {
    // Eight at the time of writing, across six panels. `ariaLabel` is required, so typecheck is the
    // real gate; this floor only stops the assertion below from passing by matching nothing.
    expect(sites.length, 'the matcher must find the SegPills call sites').toBeGreaterThanOrEqual(8)
  })

  it('has no unnamed call site', () => {
    const mute = sites.filter((s) => !/\bariaLabel=/.test(s.t))
    expect(mute.map((s) => s.f), 'SegPills without a dimension').toEqual([])
  })

  it("the 26-at-once matrix names the RULE, not just the dimension", () => {
    // A shared dimension cannot disambiguate 26 sibling groups; the rule's own label has to be in
    // the name or the fix is cosmetic on the one surface where it matters most.
    const src = stripComments(readFileSync(join(SETTINGS, 'NotificationRulesMatrix.tsx'), 'utf8'))
    expect(src).toMatch(/ariaLabel=\{`Delivery mode for \$\{r\.label\}`\}/)
  })
})

describe('the family is DERIVED, so the next pill group cannot be missed', () => {
  /** An exclusive-choice or current-item group: ANY `.map()` whose body compares its item to one piece
   *  of state and renders a button. Two corrections to the first version of this sweep, both of which
   *  hid real members:
   *
   *  🪤 IT WAS KEYED ON VARIABLE NAMES (`options|opts|MODES`). `MemoryPanel` maps `TOP_TABS` and an
   *     inline `(['all','fact',…] as const)` literal, so its tab bar, its kind filter and its opened-row
   *     marker were all invisible to it — three real members, in the panel with the most of them. A
   *     matcher keyed on what a variable is CALLED is the same mistake as a census keyed on a component
   *     name.
   *  🪤 IT SCOPED THE STATE CHECK TO A 900-CHARACTER WINDOW, which reaches past the button into
   *     neighbouring markup: one of `DiagnosticsPanel`'s two identical level pickers scored as marked
   *     purely because an unrelated element after it carried an aria attribute. A character window is
   *     not a scope — the state must be read from the button's OWN opening tag.
   *
   *  Literal comparisons (`=== true/false/null/0`) are data predicates, not selection, and are excluded:
   *  `m.downloaded === false` and `t.disabled === true` are not one-of-N groups. */
  function exclusiveGroups() {
    const LITERAL = /^(?:true|false|null|undefined|\d+)$/
    const STATE = /aria-pressed|aria-selected|aria-checked|aria-current|aria-expanded|role="(?:tab|radio|option|menuitemradio|treeitem)"/
    const out: { rel: string; state: boolean; cmp: string }[] = []
    for (const f of walk(SRC)) {
      const src = stripComments(readFileSync(f, 'utf8'))
      for (const m of src.matchAll(/\.map\(\s*\(?\s*(\w+)[^)]{0,40}\)?\s*=>\s*\{/g)) {
        const body = src.slice(m.index!, m.index! + 1100)
        const item = m[1]
        const cmp = body.match(new RegExp(`const \\w+ = (?:${item}(?:\\.\\w+)? === (\\w+)|(\\w+) === ${item}(?:\\.\\w+)?)`))
        if (!cmp) continue
        if (LITERAL.test(cmp[1] ?? cmp[2] ?? '')) continue
        // The state must live on a BUTTON's own opening tag inside this map body — `tags()` is
        // brace-aware, so it stops at the real `>` and not at one inside an arrow function.
        const buttons = [...tags(body, 'button'), ...tags(body, 'motion\\.button')]
        if (buttons.length === 0) continue
        out.push({ rel: f.slice(SRC.length + 1), state: buttons.some((t) => STATE.test(t)), cmp: cmp[0] })
      }
    }
    return out
  }

  /** Verified members whose correct marker is NOT this rail's `aria-pressed` form, so each is left for
   *  its own cycle rather than given the wrong one. Every entry was read before being listed — a false
   *  positive recorded as pending is a filed non-finding:
   *
   *  🔑 The list is EMPTY, and that is the point of keeping it typed: `TasksListPage`'s active-list pill
   *  was the last member in this rail's own form and is now marked (asserted below). An empty pending set
   *  with a live sweep above it means "swept, and nothing outstanding" — which is a different claim from
   *  a rail that simply never looked.
   *
   *  🔑 `ChatPage` and `SyntaxReference` came off this list WITHOUT being fixed here, because listing
   *  them here was a mistake of the same kind this file's history is full of: they are DISCLOSURES
   *  (`{open && …}`), and `ui/disclosureAnnounced.test.ts` is the ledger that owns that family. Keeping
   *  a second copy of its members here is how a census ends up reading as complete while its verdicts
   *  live in two files. They are now fixed and asserted THERE, under the accordion sweep that file
   *  gained for exactly this shape.
   *
   *  `ui/Combobox.tsx` came off for the ordinary reason: it declares listbox semantics now (see
   *  `ui/comboboxListbox.test.tsx`). The list may only ever shrink. */
  const PENDING = new Set<string>([])

  it('every exclusive-choice group marks its state, or is a named exception', () => {
    const groups = exclusiveGroups()
    // Vacuity floors: this matcher is doing the enumerating, so prove it resolved something, and that
    // it still sees the members whose absence it was blind to before.
    expect(groups.length, 'the sweep must find the groups').toBeGreaterThanOrEqual(14)
    expect(groups.some((g) => g.rel === 'pages/settings/settingsUI.tsx'), 'SegPills must be in scope').toBe(true)
    expect(groups.filter((g) => g.rel === 'pages/settings/MemoryPanel.tsx').length,
      'the three the name-keyed sweep could not see').toBeGreaterThanOrEqual(3)
    expect(groups.filter((g) => g.rel === 'pages/settings/DiagnosticsPanel.tsx').length,
      'both level pickers, not one').toBeGreaterThanOrEqual(2)

    const mute = groups.filter((g) => !g.state && !PENDING.has(g.rel)).map((g) => g.rel)
    expect(mute, `these convey selection visually only:\n${mute.join('\n')}`).toEqual([])
  })

  it('the pending list is not stale — every entry is still unmarked', () => {
    // A pending entry that has since been fixed must be pruned, or the list quietly becomes an
    // allowlist for work already done.
    const groups = exclusiveGroups()
    const fixed = [...PENDING].filter((rel) => {
      const mine = groups.filter((g) => g.rel === rel)
      return mine.length > 0 && mine.every((g) => g.state)
    })
    expect(fixed, `these are marked now — prune them from PENDING:\n${fixed.join('\n')}`).toEqual([])
  })

  it("the settings panels that had no selection state now have it", () => {
    const groups = exclusiveGroups()
    for (const rel of ['pages/settings/MemoryPanel.tsx', 'pages/settings/DiagnosticsPanel.tsx']) {
      const mine = groups.filter((g) => g.rel === rel)
      expect(mine.length, `${rel} must still be in scope`).toBeGreaterThan(0)
      const mute = mine.filter((g) => !g.state).map((g) => g.cmp)
      expect(mute, `${rel} still conveys selection visually only:\n${mute.join('\n')}`).toEqual([])
    }
  })
})

describe('the last two current-item markers in the tree', () => {
  const codeOf = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it("the task-list pill says which list the page is showing", () => {
    // Its `bg-primary text-on-primary` was the only cue. This rail's own form applies: the name carries
    // the dimension, and it sits on the PICK button — the pill wrapper also holds Reset, so a state on
    // the wrapper would describe two controls at once.
    const code = codeOf('pages/tasks/TasksListPage.tsx')
    expect(code).toMatch(/aria-label=\{`Task list: \$\{l\.name\}`\} aria-pressed=\{isActive\}/)
    expect(code, 'and the Reset button keeps its own separate name')
      .toMatch(/aria-label=\{`Reset list \$\{l\.name\}`\}/)
  })

  it("the file tree says which file is open, and only where the tint claims it", () => {
    // `aria-current` mirrors the same `isActive` the 14% tint uses, so the announcement and the colour
    // cannot disagree — the failure mode of adding a second, independently-computed condition.
    const code = codeOf('pages/files/browse/FileTree.tsx')
    expect(code).toMatch(/aria-current=\{isActive \? 'page' : undefined\}/)
    expect(code, 'the row still declares folder expansion separately')
      .toMatch(/aria-expanded=\{entry\.is_dir \? open : undefined\}/)
    // One flag, two cues: if the tint ever stops using `isActive`, this pairing is what breaks.
    expect(code).toMatch(/isActive \? 'color-mix\(in srgb, var\(--color-primary\) 14%, transparent\)'/)
  })
})
