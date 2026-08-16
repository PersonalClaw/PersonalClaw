import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { PanelRight } from 'lucide-react'
import { HeaderControl } from './HeaderActions'
import { IconButton } from './IconButton'
import { TileButton } from './TileButton'

// ── Three primitives modelled "this control is on" and told nobody ───────────────────────────
//
// Cycle 128 measured 48 boolean-toggling buttons and closed 14, leaving 34 to CLASSIFY (the rail's
// ceiling). Reading them turned the remainder inside out: **most are not raw buttons at all.** They are
// primitives that already take an `active` prop — and every one of them spent it on a colour:
//
//   `HeaderControl`  ui/HeaderActions.tsx   **14 call sites, 7 files**  → `active ? variants.secondary : …`
//   `FilterChip`     knowledge/KnowledgeListPage.tsx   **8 call sites** → `style={active ? selected : …}`
//   `IconButton`     ui/IconButton.tsx      **2 call sites** (composer optimize + mic)
//
// 🔑 SO THE FIX BELONGS IN THREE PRIMITIVES, NOT AT 24 CALL SITES — and the contract is already written
// down in `Button`'s own doc: *"a button acting as a SELECTED/UNSELECTED choice must announce that state,
// or a screen-reader user hears an identical label for the row they are on and the row they are not."*
// `Segmented`/`SegToggle` already ship `aria-pressed` for exclusive choice; this brings the `active`
// family onto the same contract instead of inventing one.
//
// Driven, parent worktree vs this one (`grep -c 'aria-pressed={active}'` = 0 there, 1 here per file):
//
//   surface        aria-pressed nodes   one live flip
//   #/knowledge    **0 → 9** (2 true)   the type chips are an exclusive group; clicking the active one
//                                       is a no-op, as it should be (true → true)
//   #/files        **0 → 4** (4 true)   **true → false**
//   #/chat         **0 → 6** (0 true)   **false → true**
//   #/inbox        **0 → 4** (0 true)   **false → true**
//
// 🪤 THE NODE COUNTS ARE NOT CONTROL COUNTS. `#/files` shows four nodes all named "Hide explorer" because
// `HeaderActions` renders the same control at several responsive tiers. Four nodes, one control — worth
// saying plainly rather than implying the header has four toggles.
//
// 🔑 AND `active` STAYS OPTIONAL. A header action that is not a toggle (Save, Run build, Close) passes no
// `active`, so it must emit NO `aria-pressed` at all — announcing `"false"` would tell assistive tech that
// a plain action has an off state.
//
// ── Cycle 153: THE SAME FAMILY, THREE SITES CYCLE 128 DID NOT REACH ─────────────────────────────────
//
// `#/settings/design` is where this costs the most, because the whole page IS selection. Read out of
// Chrome's accessibility tree: **3 of 89 buttons exposed any state** — the Mode row — and everything the
// user actually picks announced like everything they did not:
//
//   the 12 scheme tiles      "Coral, button … Honey, button"  ✓ glyph + coral outline, no state
//   the 3 personality cards  identical to each other          ✓ glyph + border, no state
//   the token select pills   **"dm-sans, button"**            coral fill, no state, and no group name
//
// The pills were two defects at once: the name was the bare VALUE, so "waves" / "hex" / "claude" gave no
// hint of Background / Arrangement / Dot shape. `bento`'s `SegToggle` already solves both, one screen
// over: `aria-label={`${ariaLabel}: ${o.label}`} aria-pressed={o.key === value}`.
//
//   after, same probe: **3 → 40 buttons expose state** on that surface, and the names carry their group.
//   6/6 captures pixel-identical (both themes, desktop + phone) — semantics only.
//
// 🪤 AND ONE `active` TURNED OUT TO BE INERT, WHICH THE `aria-pressed` RULE MADE VISIBLE. `TileButton`'s
// other consumer is `ArtifactCard`, and `ArtifactsSection` passes **`activeSlug={null}`** hard-coded —
// the grid only renders when nothing is open. So `active` could never be true, and wiring the attribute
// would have put a permanent `aria-pressed="false"` on five tiles with no on state, which is exactly what
// the rule above forbids. The plumbing is deleted (`ArtifactCard.active`, `ArtifactGrid.activeSlug`, the
// call site) rather than announced: `#/artifacts` now reports **zero** `aria-pressed` nodes, and its
// captures are unchanged because the `border-primary/60` it selected never applied either.

describe('HeaderControl announces the state its colour already showed', () => {
  it('is pressed when active', () => {
    render(<HeaderControl icon={PanelRight} label="Activity" active onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Activity' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('is unpressed when explicitly inactive', () => {
    render(<HeaderControl icon={PanelRight} label="Activity" active={false} onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Activity' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('says nothing at all when the control is not a toggle', () => {
    // The distinction that keeps this honest: no `active` prop → no state claim.
    render(<HeaderControl icon={PanelRight} label="Run build" onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Run build' }).hasAttribute('aria-pressed')).toBe(false)
  })
})

describe('IconButton does the same, and keeps what it already announced', () => {
  it('is pressed when active', () => {
    render(<IconButton icon={PanelRight} label="Optimize prompt" active onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Optimize prompt' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('omits the attribute for a plain icon action', () => {
    render(<IconButton icon={PanelRight} label="Close" onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Close' }).hasAttribute('aria-pressed')).toBe(false)
  })

  it('still carries its name and its disabled contract', () => {
    // Cycle 119's work must survive: the reason rides `title`, the name stays findable.
    render(<IconButton icon={PanelRight} label="Optimize prompt" disabled disabledReason="Type something first" onClick={vi.fn()} />)
    const el = screen.getByRole('button', { name: 'Optimize prompt' })
    expect(el.getAttribute('aria-disabled')).toBe('true')
    expect(el.getAttribute('title')).toBe('Optimize prompt — Type something first')
  })
})

describe('TileButton — the card-shaped member of the family', () => {
  it('is pressed when the tile is the selected one', () => {
    render(<TileButton active onClick={vi.fn()} ariaLabel="Retro Terminal">tile</TileButton>)
    expect(screen.getByRole('button', { name: 'Retro Terminal' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('is unpressed when it is a selectable tile that is not selected', () => {
    render(<TileButton active={false} onClick={vi.fn()} ariaLabel="Claw Arcade">tile</TileButton>)
    expect(screen.getByRole('button', { name: 'Claw Arcade' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('says nothing when the tile is not a selection at all', () => {
    // Same distinction as HeaderControl: a plain "open this" tile has no off state to report.
    render(<TileButton onClick={vi.fn()} ariaLabel="open me">tile</TileButton>)
    expect(screen.getByRole('button', { name: 'open me' }).hasAttribute('aria-pressed')).toBe(false)
  })
})

describe("the design panel's own two hand-rolled selectors", () => {
  const SRC = join(process.cwd(), 'src')
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

  it('the scheme tile announces which scheme is on', () => {
    expect(read('pages/settings/DesignPanel.tsx')).toMatch(/<button type="button" onClick=\{onPick\} aria-pressed=\{active\}/)
  })

  it('the token select pill announces its state AND names its group', () => {
    const src = read('ui/TokenControls.tsx')
    expect(src, 'the bare value is not a name — "dm-sans" told nobody it was Font family')
      .toMatch(/aria-label=\{`\$\{token\.label\}: \$\{opt\}`\}/)
    expect(src).toMatch(/aria-pressed=\{on\}/)
  })

  it('the Mode row it converged onto is unchanged', () => {
    // The canonical form lives 20 lines above the scheme tiles; if it moves, reconcile rather than fork.
    expect(read('pages/settings/DesignPanel.tsx')).toMatch(/aria-label=\{`Mode: \$\{m\.label\}`\} aria-pressed=\{on\}/)
  })
})

describe('an `active` that can never be true is deleted, not announced', () => {
  const SRC = join(process.cwd(), 'src')
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

  it('the artifacts grid no longer threads a hard-coded selection', () => {
    for (const rel of ['pages/artifacts/ArtifactCard.tsx', 'pages/artifacts/ArtifactGrid.tsx', 'pages/artifacts/ArtifactsSection.tsx']) {
      expect(read(rel), `${rel} still carries the inert prop`).not.toMatch(/activeSlug|active=\{a\.slug/)
    }
  })

  it('and the card asks TileButton for no state', () => {
    // If a real selection ever arrives on that grid, this is the line to change — deliberately, with a
    // writer for the value, rather than by re-adding a prop nothing sets.
    expect(read('pages/artifacts/ArtifactCard.tsx')).toMatch(/<TileButton onClick=\{\(\) => onOpen\(art\)\} title=\{art\.name\} ariaLabel=\{art\.name\}/)
  })
})

describe('the population this reaches, so the primitives were the right place', () => {
  const SRC = join(process.cwd(), 'src')
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  /** 🪤 Matched to the SELF-CLOSING `/>`, never to the first `>` — `onClick={() => …}` contains one, and
   *  that mistake produced four false negatives earlier in this session. */
  const callSites = (prim: string) =>
    walk(SRC).flatMap((abs) =>
      [...readFileSync(abs, 'utf8').matchAll(new RegExp(`<${prim}\\b[\\s\\S]{0,400}?/>`, 'g'))]
        .filter((m) => /\bactive=/.test(m[0])))

  it('HeaderControl has 14 active call sites', () => {
    expect(callSites('HeaderControl').length).toBeGreaterThanOrEqual(14)
  })

  it('FilterChip has 8, and now announces them', () => {
    expect(callSites('FilterChip').length).toBeGreaterThanOrEqual(8)
    const src = readFileSync(join(SRC, 'pages/knowledge/KnowledgeListPage.tsx'), 'utf8')
    expect(src).toMatch(/<button type="button" onClick=\{onClick\} aria-pressed=\{active\}/)
  })

  it('IconButton has 2 — small, and one of them is a recording state', () => {
    // "Recording" vs "not recording" is precisely what a tint cannot convey.
    expect(callSites('IconButton').length).toBeGreaterThanOrEqual(2)
  })

  it('each primitive binds the attribute to its own `active` prop', () => {
    for (const rel of ['ui/HeaderActions.tsx', 'ui/IconButton.tsx']) {
      expect(readFileSync(join(SRC, rel), 'utf8'), `${rel} must bind aria-pressed to active`)
        .toMatch(/aria-pressed=\{active\}/)
    }
  })
})

// ── The other half of the contract: a pressed control must name the ACTION ────────────────────
//
// Wiring `aria-pressed={active}` fixed controls that said nothing about their state. It also created
// a second, quieter fault at three call sites that were ALREADY trying to convey state — by flipping
// their label to the past participle. With `aria-pressed` now in place, `label={fav ? 'Favorited' :
// 'Favorite'}` announces "Favorited, pressed": the state twice, and the one thing the user needs —
// what this press will DO — never. The ARIA toggle-button pattern is explicit that a control changes
// its label OR carries `aria-pressed`, not both. It also made these the only header controls whose
// name changes under the user, so "click Favorite" stops being findable once it is on.
//
// Canonical form (five sites, unchanged by this): constant label + `active` → "Activity",
// "Chat history", "Inbox settings", "Details", "More details".
// Deliberate NON-member: knowledge's read-state control cycles unread → reading → read, so its label
// must say what the next press does; a constant label could not. It is excluded by name below.
describe('a pressed header control names the action, not the state', () => {
  const SRC = join(process.cwd(), 'src')
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
  /** Source with comments stripped — this very block names the forbidden strings. */
  const code = (rel: string) => read(rel)
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^[ \t]*\/\/.*$/gm, '')

  it('renders one stable name whether it is on or off', () => {
    const { unmount } = render(<HeaderControl icon={PanelRight} label="Favorite" active onClick={vi.fn()} />)
    const on = screen.getByRole('button', { name: 'Favorite' })
    expect(on.getAttribute('aria-pressed')).toBe('true')
    unmount()
    render(<HeaderControl icon={PanelRight} label="Favorite" active={false} onClick={vi.fn()} />)
    const off = screen.getByRole('button', { name: 'Favorite' })
    expect(off.getAttribute('aria-pressed')).toBe('false')
  })

  it("knowledge's three two-state toggles carry constant labels", () => {
    const src = code('pages/knowledge/KnowledgeDetail.tsx')
    for (const [icon, label] of [['Star', 'Favorite'], ['Pin', 'Pin'], ['Archive', 'Archive']]) {
      expect(src, `${icon} toggle should pass a constant label="${label}"`)
        .toMatch(new RegExp(`icon=\\{${icon}\\}\\s+label="${label}"`))
    }
  })

  it('no header control restates its state in its label', () => {
    const src = code('pages/knowledge/KnowledgeDetail.tsx')
    for (const participle of ['Favorited', 'Pinned', 'Archived']) {
      expect(src, `"${participle}" is a state, not an action — aria-pressed already carries it`)
        .not.toMatch(new RegExp(`label=\\{[^}]*'${participle}'`))
    }
  })

  it('the read-state cycle is left alone — three states need an action label', () => {
    // Guards the DISTINCTION, so a later sweep does not "converge" it and lose the affordance.
    expect(code('pages/knowledge/KnowledgeDetail.tsx')).toMatch(/'Reading — mark read'/)
  })

  // Vacuity floor: every assertion above is a source scan, so prove the scanned text is real.
  it('the scanned source is real (guard against a vacuous pass)', () => {
    const src = code('pages/knowledge/KnowledgeDetail.tsx')
    expect(src).toContain('<HeaderControl')
    expect(src).toContain('active={!!full.favorited}')
    // And prove comment-stripping actually ran, by looking for a phrase that exists ONLY in this
    // block's prose. The needle is ASSEMBLED rather than written out: a literal
    // `not.toContain('…')` puts the phrase into the file it is scanning, so the assertion could
    // never pass — which is the same self-reference this rail exists to catch, and it caught it here.
    expect(code('ui/activeMeansPressed.test.tsx')).not.toContain(['quieter', 'fault'].join(' '))
  })
})
