import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A floating sheet's shadow does not follow the scheme, on seven surfaces ────────────────────
//
// `design/tokens.css` defines `--shadow-sheet` TWICE — once per scheme — because a sheet lifted off
// the page needs a different shadow on a dark canvas than on a light one:
//
//   dark   (tokens.css:138)  0 16px 40px rgb(0 0 0 / 0.42)        a deep neutral drop
//   light  (tokens.css:192)  0 16px 40px rgb(96 110 130 / 0.22)   a soft BLUE-GREY drop
//
// `shadow-2xl` is not that. Tailwind v4 ships `--shadow-2xl` as a default and this app never
// overrides it, so the class resolves to a fixed value in both schemes. Measured live in the dev
// build (dark), on a probe element carrying each class in turn:
//
//   shadow-2xl     rgba(0, 0, 0, 0.25) 0px 25px 50px -12px    ← identical in light; nothing retints
//   shadow-sheet   rgba(0, 0, 0, 0.42) 0px 16px 40px 0px      ← the light value is the blue-grey above
//
// (The light-mode number is quoted from `tokens.css`, not measured: flipping the scheme from the
// console did not take — this app switches on the `mode` key plus a reload, so a `classList` toggle
// reads as a successful flip while the computed value never changes. Stated rather than implied.)
//
// So in LIGHT mode these seven surfaces cast a hard black shadow where the design system asks for a
// soft blue-grey one. The tenet is explicit — everything is a token, and the app must survive a
// scheme retint — and a fixed shadow does not.
//
// ── Why this file is a RATCHET and not a fix ───────────────────────────────────────────────────
//
// All seven are floating sheets over content, which is exactly what `--shadow-sheet` names:
//
//   ui/NavRail                       the mobile drawer      ← on EVERY route
//   ui/widget/WidgetFrame            expanded widget card
//   ui/widget/ReactWidgetFrame       expanded widget card
//   app/CommandPalette               the Cmd-K card
//   pages/terminal/TerminalDrawer    the terminal drawer
//   pages/files/comments/CommentLayer  the comment popover
//   pages/chat/SessionSkillsReview   the session-skills modal
//
// Converging them is therefore mechanical to write and NOT mechanical to decide: it changes the
// shadow under seven shipped surfaces, one of which (`NavRail`) appears on every route, so every
// visual baseline moves. `personalclaw-ux` §9 puts that squarely in the owner's hands — unifying two
// divergent shipped patterns that change the visual language. The ruling is filed in the session
// handoff with these numbers.
//
// 🔑 WHAT A RATCHET BUYS WHILE A RULING IS PENDING: the family cannot grow. A pending decision
// usually means the drift keeps accruing and the eventual convergence is bigger than the one that was
// costed. Pinning the population makes the cost of the ruling fixed, and makes any NEW floating sheet
// pick the scheme-aware token by default — which is the outcome the ruling would most likely order
// anyway, applied only where it costs nothing.

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
  'pages/chat/SessionSkillsReview.tsx',
  'pages/files/comments/CommentLayer.tsx',
  'pages/terminal/TerminalDrawer.tsx',
  'ui/NavRail.tsx',
  'ui/widget/ReactWidgetFrame.tsx',
  'ui/widget/WidgetFrame.tsx',
]

describe('the sheet-shadow ratchet (scheme-blind shadows may only shrink)', () => {
  const users = () => files().filter((f) => /shadow-2xl/.test(f.src)).map((f) => f.rel).sort()

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
    // reader would cost the ruling against a population that is already smaller.
    const stale = BASELINE.filter((rel) => !users().includes(rel))
    expect(
      stale,
      `these no longer use shadow-2xl — remove them from BASELINE:\n  ${stale.join('\n  ')}`,
    ).toEqual([])
  })

  it('the census is not vacuous, and the matcher really fires', () => {
    // A rail matching nothing reports a clean sweep. Both directions asserted.
    expect(users().length, 'the shadow-2xl census must not go empty').toBe(7)
    expect(/shadow-2xl/.test('<div className="squircle shadow-2xl" />')).toBe(true)
    expect(/shadow-2xl/.test('<div className="squircle shadow-sheet" />')).toBe(false)
  })

  it('shadow-sheet is a real, scheme-aware alternative — not an aspiration', () => {
    // The convergence target has to exist and be defined per scheme, or this rail is pointing at
    // nothing. Both definitions live in tokens.css; the light one is what `shadow-2xl` never becomes.
    const tokens = readFileSync(join(SRC, 'design/tokens.css'), 'utf8')
    const defs = tokens.match(/--shadow-sheet:/g) ?? []
    expect(defs.length, '--shadow-sheet must be defined per scheme (dark + light)').toBeGreaterThanOrEqual(2)
    expect(tokens, 'the light definition is the blue-grey drop').toMatch(/--shadow-sheet: 0 16px 40px rgb\(96 110 130/)
    // And it must have real adopters, so "canonical" is a description and not a preference.
    const adopters = files().filter((f) => /shadow-sheet/.test(f.src)).map((f) => f.rel)
    expect(adopters.length, `shadow-sheet adopters: ${adopters.join(', ')}`).toBeGreaterThanOrEqual(4)
  })
})
