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
  /** Primitives that declare the selection state for their caller. A row rendered through one of these
   *  is satisfied by construction; each is proven to declare it in the test below. */
  const DELEGATES = ['TileButton', 'SegToggle', 'SegPills', 'Segmented', 'WidthPill', 'MenuRow']

  function exclusiveGroups() {
    const LITERAL = /^(?:true|false|null|undefined|\d+)$/
    const STATE = /aria-pressed|aria-selected|aria-checked|aria-current|aria-expanded|role="(?:tab|radio|option|menuitemradio|treeitem)"/
    const out: { rel: string; state: boolean; cmp: string; via: 'equality' | 'membership' }[] = []
    for (const f of walk(SRC)) {
      const src = stripComments(readFileSync(f, 'utf8'))
      for (const m of src.matchAll(/\.map\(\s*\(?\s*(\w+)[^)]{0,40}\)?\s*=>\s*\{/g)) {
        const body = src.slice(m.index!, m.index! + 1100)
        const item = m[1]
        const eq = body.match(new RegExp(`const \\w+ = (?:${item}(?:\\.\\w+)? === (\\w+)|(\\w+) === ${item}(?:\\.\\w+)?)`))
        // 🪤 THE THIRD CORRECTION, AND THE SHARPEST: IT WAS KEYED ON THE COMPARISON OPERATOR.
        //    A one-of-N or multi-select group does not have to compare with `===`. When the selection
        //    is held as a collection, the row asks whether it is IN it — `activeProviders.includes(p.name)`,
        //    `tagFilter.has(t.id)`, `selected.includes(o.id)`. That is the same interaction in a spelling
        //    `===` cannot see, and it hid SIX groups, FIVE of them stateless: `SearchPanel`'s provider
        //    bind (a genuine one-of-N), `ChatPage`'s knowledge picker and tag filter, `AgentForm`'s
        //    checklist and `LoopPlanReview`'s phase caps. The sixth was already correct (`MenuRow`),
        //    which is how we know the spelling was the blind spot and not the fix.
        //    Equality with a scalar, membership of a set: enumerate BOTH, or the count reads complete.
        const member = body.match(new RegExp(
          `const \\w+ = !?\\w+\\.(?:includes|has)\\(\\s*${item}(?:\\.\\w+)?\\s*\\)`
          + `|const \\w+ = \\w+\\.indexOf\\(\\s*${item}(?:\\.\\w+)?\\s*\\)\\s*(?:>= 0|!== -1)`))
        const cmp = eq ?? member
        if (!cmp) continue
        // The literal guard belongs to the equality form only — a membership test's left side is a
        // collection identifier, never `true`/`null`/a number.
        if (eq && LITERAL.test(eq[1] ?? eq[2] ?? '')) continue
        // The state must live on the ROW's own opening tag inside this map body — `tags()` is
        // brace-aware, so it stops at the real `>` and not at one inside an arrow function.
        // 🪤 MEASURED, NOT ASSUMED: widening this to `div`/`span`/`a` rows added TWO false positives and
        // ZERO true ones. `ChatPage`'s `isLast = i === turns.length - 1` is POSITIONAL, and
        // `TerminalPage`'s `visible = t.id === active` gates which terminal PANE is mounted — neither is a
        // control, and neither takes a selection state. The one non-button row in the tree
        // (`TerminalDrawer`'s `div role="tab"`) already declares its own. Buttons plus delegation is the
        // whole population.
        //
        // A positional comparison (`i === turns.length - 1`) survives the literal guard above, because
        // the captured side is an identifier — but it needs no exclusion of its own while rows are
        // buttons-only: mutation-checked, and removing such a guard changed nothing. An inert guard in a
        // rail is the same defect this file exists to catch, so there isn't one.
        const rows = [...tags(body, 'button'), ...tags(body, 'motion\\.button')]
        // 🔑 OR THE ROW DELEGATES IT. A row rendered through a state-aware primitive declares nothing
        // itself — the primitive does, one level down — and every one of those reads as "silent" to a
        // matcher that only inspects this body. `PersonalityPicker` is the case that proved it:
        // `<TileButton active={active}>`, where `ui/TileButton` applies `aria-pressed={active}` (and its
        // comment records that this very picker is why). Treating an unrecognised row as a FAILURE — the
        // "obvious" hardening — would therefore cry wolf on every correct delegation in the tree, which
        // is worse than under-reporting. The delegates are named, and `the delegation list is not a
        // blanket exemption` below proves each one really declares state.
        const delegates = DELEGATES.some((c) => new RegExp(`<${c}\\b`).test(body))
        if (rows.length === 0 && !delegates) continue
        out.push({
          rel: f.slice(SRC.length + 1),
          state: delegates || rows.some((t) => STATE.test(t)),
          cmp: cmp[0],
          via: eq ? 'equality' : 'membership',
        })
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
    // 17 with delegation counted, 15 without — the two the button-only scope could not see are
    // `NotificationRulesMatrix` (renders SegPills) and `PersonalityPicker` (renders TileButton). Counting
    // them is the point: if either swaps its primitive for a raw button, it appears here unmarked.
    // 17 was the equality-only population; the membership spelling added six more (see the floor
    // asserted in the test below, which is the one that would catch that matcher silently matching
    // nothing — the whole population floor is too coarse to notice six missing).
    expect(groups.length, 'the sweep must find the groups').toBeGreaterThanOrEqual(23)
    for (const rel of ['pages/settings/NotificationRulesMatrix.tsx', 'pages/settings/PersonalityPicker.tsx']) {
      expect(groups.map((g) => g.rel), `${rel} delegates, and must still be COUNTED`).toContain(rel)
    }
    expect(groups.some((g) => g.rel === 'pages/settings/settingsUI.tsx'), 'SegPills must be in scope').toBe(true)
    expect(groups.filter((g) => g.rel === 'pages/settings/MemoryPanel.tsx').length,
      'the three the name-keyed sweep could not see').toBeGreaterThanOrEqual(3)
    expect(groups.filter((g) => g.rel === 'pages/settings/DiagnosticsPanel.tsx').length,
      'both level pickers, not one').toBeGreaterThanOrEqual(2)

    const mute = groups.filter((g) => !g.state && !PENDING.has(g.rel)).map((g) => g.rel)
    expect(mute, `these convey selection visually only:\n${mute.join('\n')}`).toEqual([])
  })

  it('the membership spelling is swept, and every one of its groups is marked', () => {
    // 🔑 THE FLOOR THAT MATTERS FOR A NEW MATCHER. A regex that resolves to nothing produces exactly
    // the same green as a clean tree, and this one interpolates the map's item name — one escaping
    // slip and it silently matches zero. Six is what was measured when it was written: five stateless
    // (now fixed) plus `ChatPage`'s artifact picker, which was already correct via `MenuRow`.
    const groups = exclusiveGroups()
    const member = groups.filter((g) => g.via === 'membership')
    expect(member.length, 'the membership matcher must resolve its own population').toBeGreaterThanOrEqual(6)
    for (const rel of [
      'pages/settings/SearchPanel.tsx',
      'pages/ChatPage.tsx',
      'pages/agents/AgentForm.tsx',
      'pages/loops/LoopPlanReview.tsx',
    ]) {
      expect(member.map((g) => g.rel), `${rel} holds its selection in a collection — it must be COUNTED`)
        .toContain(rel)
    }
    const mute = member.filter((g) => !g.state).map((g) => `${g.rel}  ${g.cmp}`)
    expect(mute, `these hold selection in a collection and announce nothing:\n${mute.join('\n')}`).toEqual([])
  })

  it('each of those groups also states its DIMENSION, not just its state', () => {
    // `aria-pressed` alone answers "is this one on?" and never "on for WHAT?". Four of the five sat in
    // a group whose only label was a `<span>` — text that labels nothing — and `SearchPanel` renders
    // FOUR sibling lists on one panel, where an unnamed group is genuinely ambiguous. Each name is
    // asserted at its own site so a later edit cannot quietly drop it.
    const named: [string, RegExp][] = [
      ['pages/settings/SearchPanel.tsx', /role="group" aria-label=\{`\$\{meta\.label\} provider`\}/],
      ['pages/ChatPage.tsx', /role="group" aria-label="Knowledge to attach"/],
      ['pages/ChatPage.tsx', /role="group" aria-label="Filter by tag"/],
      ['pages/agents/AgentForm.tsx', /role="group" aria-label=\{label\}/],
      ['pages/loops/LoopPlanReview.tsx', /role="group" aria-label=\{label\}/],
    ]
    for (const [rel, re] of named) {
      const src = stripComments(readFileSync(join(SRC, rel), 'utf8'))
      expect(src, `${rel} must name the group its options belong to`).toMatch(re)
    }
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

describe('delegation is a real path, not a hole in the sweep', () => {
  const codeOf = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

  /** Where each delegate actually declares the state it accepts. If a name is added to `DELEGATES`
   *  without landing here, the sweep has gained an exemption rather than an understanding. */
  const DECLARED_IN: Record<string, string> = {
    TileButton: 'ui/TileButton.tsx',
    SegToggle: 'pages/settings/bento.tsx',
    SegPills: 'pages/settings/settingsUI.tsx',
    Segmented: 'ui/Segmented.tsx',
    WidthPill: 'ui/WidthPill.tsx',
    MenuRow: 'ui/Popover.tsx',
  }

  it('every delegate declares a selection state for its caller', () => {
    // The sweep counts a row as satisfied when it renders one of these. That is only true if the
    // primitive really applies the attribute — otherwise a whole family of call sites goes quiet behind
    // a name on a list.
    for (const [name, rel] of Object.entries(DECLARED_IN)) {
      const src = codeOf(rel)
      expect(src, `${name} (${rel}) must declare a selection state`)
        .toMatch(/aria-pressed|aria-selected|aria-checked|aria-current|role="(?:tab|option|radio|menuitemradio)"/)
    }
  })

  it("the delegate list and the list the sweep uses are the same list", () => {
    // Two copies of this list would let the sweep exempt a name that nothing proves. Read the sweep's
    // own array out of this file rather than restating it.
    const self = readFileSync(join(SRC, 'pages/settings/exclusiveChoiceNamed.test.tsx'), 'utf8')
    const declared = self.match(/const DELEGATES = \[([^\]]+)\]/)
    expect(declared, "the sweep's DELEGATES array must be findable").toBeTruthy()
    const names = [...declared![1].matchAll(/'([A-Za-z]+)'/g)].map((m) => m[1]).sort()
    expect(names, 'every delegate must be proven above').toEqual(Object.keys(DECLARED_IN).sort())
  })

  it("PersonalityPicker is the case that proved delegation — and it is NOT a defect", () => {
    // `<TileButton active={active}>` declares nothing itself; `ui/TileButton` applies
    // `aria-pressed={active}`, and its own comment records that this picker is why. A sweep that failed
    // on unrecognised rows would have reported this correct code as broken.
    // 🪤 `<TileButton[^>]*active=...>` FAILS here, and for the reason this file's own `tags()` helper was
    // written: the call contains `onClick={() => pick(p)}`, so `[^>]*` stops at the arrow's `>` before
    // reaching the prop. Use the brace-aware extractor, always.
    const picker = stripComments(codeOf('pages/settings/PersonalityPicker.tsx'))
    const tile = tags(picker, 'TileButton')
    expect(tile.length, 'the picker must still render TileButton').toBeGreaterThan(0)
    expect(tile.some((t) => /active=\{active\}/.test(t)), 'and hand it the selection').toBe(true)
    expect(codeOf('ui/TileButton.tsx'), 'and the primitive is where the state lives')
      .toMatch(/aria-pressed=\{active\}/)
  })
})
