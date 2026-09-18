import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A floating sheet's shadow does not follow the scheme, on six surfaces ──────────────────────
//
// `design/tokens.css` defines `--shadow-sheet` TWICE — once per scheme — because a sheet lifted off
// the page needs a different drop on a dark canvas than on a light one:
//
//   dark   (tokens.css:194)  0 16px 40px rgb(0 0 0 / 0.42)        a deep neutral drop
//   light  (tokens.css:295)  0 16px 40px rgb(96 110 130 / 0.22)   a soft BLUE-GREY drop
//
// `shadow-2xl` is not that. Tailwind (4.3.3, per the root lockfile) ships `--shadow-2xl` as a
// default and this app never overrides it — `grep -rn -- '--shadow-2xl' web/src` returns nothing —
// so the class resolves to the same fixed rgb(0 0 0 / 0.25) drop in BOTH schemes. Nothing retints
// it. In light mode these six surfaces therefore cast a hard black shadow where the design system
// asks for a soft blue-grey one, and the tenet is explicit: everything is a token, and the app must
// survive a scheme retint.
//
// The two values above are quoted from `tokens.css`, not measured in a browser. Stated rather than
// implied, because a `classList` scheme toggle from the console reads as a successful flip while the
// computed value never changes — this app switches on the `mode` key plus a reload.
//
// ── Why this file is a RATCHET and not a fix ───────────────────────────────────────────────────
//
// All six are floating sheets over content, which is exactly what `--shadow-sheet` names:
//
//   ui/NavRail                         the mobile drawer      ← on EVERY route
//   ui/widget/WidgetFrame              expanded widget card
//   ui/widget/ReactWidgetFrame         expanded widget card
//   app/CommandPalette                 the Cmd-K card
//   pages/terminal/TerminalDrawer      the terminal drawer
//   pages/files/comments/CommentLayer  the comment popover
//
// Converging them is mechanical to WRITE and not mechanical to DECIDE: it changes the shadow under
// six shipped surfaces, one of which (`NavRail`) appears on every route, so every visual baseline
// moves. `personalclaw-ux` §9 puts that squarely in the owner's hands — unifying two divergent
// shipped patterns that change the visual language.
//
// 🔑 WHAT A RATCHET BUYS WHILE A RULING IS PENDING: the family cannot grow. A pending decision
// usually means the drift keeps accruing and the eventual convergence is bigger than the one that
// was costed. Pinning the population makes the cost of the ruling fixed, and makes any NEW floating
// sheet pick the scheme-aware token by default — the outcome the ruling would most likely order
// anyway, applied only where it costs nothing.
//
// 🪤 THE FAMILY WAS SEVEN AND IS NOW SIX, AND THAT IS WHY THE COUNT IS RE-MEASURED HERE RATHER THAN
// COPIED. `pages/tasks/dropTargetSignal.test.tsx` still says "seven floating sheets" in its prose:
// `pages/chat/SessionSkillsReview.tsx` carried `shadow-2xl` when that sentence was written and today
// carries no shadow class at all. Re-measured on 2026-09-18: the census is six. Per
// `railFloors.test.ts`'s taxonomy this is a type (a) MEASURED POPULATION, not a vacuity guard, so
// the assertion below sits exactly at the number instead of leaving itself slack.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const files = () =>
  walk(SRC).map((abs) => ({ rel: abs.slice(SRC.length + 1), src: readFileSync(abs, 'utf8') }))

/** The scheme-blind shadow, as it stands today. This list may only SHRINK. */
const BASELINE = [
  'app/CommandPalette.tsx',
  'pages/files/comments/CommentLayer.tsx',
  'pages/terminal/TerminalDrawer.tsx',
  'ui/NavRail.tsx',
  'ui/widget/ReactWidgetFrame.tsx',
  'ui/widget/WidgetFrame.tsx',
]

describe('the sheet-shadow ratchet (scheme-blind shadows may only shrink)', () => {
  const users = () =>
    files()
      .filter((f) => /shadow-2xl/.test(f.src))
      .map((f) => f.rel)
      .sort()

  it('no NEW surface takes the scheme-blind shadow', () => {
    // THE RATCHET. A floating sheet added after this should reach for `shadow-sheet`, which retints
    // with the scheme; `shadow-2xl` is Tailwind's fixed default and this app never overrides it.
    const added = users().filter((rel) => !BASELINE.includes(rel))
    expect(
      added,
      `these use shadow-2xl, which does not follow the scheme — use shadow-sheet:\n  ${added.join('\n  ')}`,
    ).toEqual([])
  })

  it('the baseline shrinks honestly — a converged file must leave the list', () => {
    // Without this, the list would keep naming files that no longer have the defect, and the next
    // reader would cost the ruling against a population that is already smaller. This is exactly
    // how `SessionSkillsReview.tsx` went unnoticed for a month.
    const stale = BASELINE.filter((rel) => !users().includes(rel))
    expect(
      stale,
      `these no longer use shadow-2xl — remove them from BASELINE:\n  ${stale.join('\n  ')}`,
    ).toEqual([])
  })

  it('the census is not vacuous, and the matcher really fires', () => {
    // A rail matching nothing reports a clean sweep. Both directions asserted, and the population
    // sits at its measured value so a seventh surface cannot slip in under a `>N` floor.
    expect(users().length, 'the shadow-2xl census must not go empty').toBe(6)
    expect(/shadow-2xl/.test('<div className="squircle shadow-2xl" />')).toBe(true)
    expect(/shadow-2xl/.test('<div className="squircle shadow-sheet" />')).toBe(false)
    // And the scan reached the tree at all, rather than an empty directory listing.
    expect(walk(SRC).length, 'the .tsx walk must find the source tree').toBeGreaterThan(200)
  })

  it('shadow-sheet is a real, scheme-aware alternative — not an aspiration', () => {
    // The convergence target has to exist and be defined per scheme, or this rail points at nothing.
    // Both definitions live in tokens.css; the light one is what `shadow-2xl` never becomes.
    const tokens = readFileSync(join(SRC, 'design/tokens.css'), 'utf8')
    const defs = tokens.match(/--shadow-sheet:/g) ?? []
    expect(defs.length, '--shadow-sheet must be defined per scheme (dark + light)').toBe(2)
    expect(tokens, 'the light definition is the blue-grey drop').toMatch(
      /--shadow-sheet: 0 16px 40px rgb\(96 110 130/,
    )
    expect(tokens, 'the dark definition is the deep neutral drop').toMatch(
      /--shadow-sheet: 0 16px 40px rgb\(0 0 0/,
    )
    // And it must have real adopters, so "canonical" is a description and not a preference.
    // Six on 2026-09-18: TasksListPage, Modal, SnipOverlay, SpotlightTour, UpdateProgressOverlay,
    // dialog/DialogShell. Floored rather than pinned — this list is meant to GROW as sheets
    // converge, and pinning it would red every convergence.
    const adopters = files().filter((f) => /shadow-sheet/.test(f.src)).map((f) => f.rel)
    expect(adopters.length, `shadow-sheet adopters: ${adopters.join(', ')}`).toBeGreaterThanOrEqual(6)
  })

  it('the app does not override Tailwind’s fixed shadow, so the finding still holds', () => {
    // The whole premise is that `shadow-2xl` cannot retint. If someone later defines
    // `--shadow-2xl` per scheme, this rail is measuring a defect that no longer exists and should
    // be deleted rather than kept green.
    const overrides = files()
      .concat([{ rel: 'design/tokens.css', src: readFileSync(join(SRC, 'design/tokens.css'), 'utf8') }])
      .filter((f) => /--shadow-2xl:/.test(f.src))
      .map((f) => f.rel)
    expect(
      overrides,
      `--shadow-2xl is overridden here, so shadow-2xl may now be scheme-aware and this rail is stale:\n  ${overrides.join('\n  ')}`,
    ).toEqual([])
  })
})
