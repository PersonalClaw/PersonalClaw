import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The tag row stops withholding two things ──────────────────────────────────────────────────
//
// Both measured in Chromium on a populated taxonomy (6 tags), and both invisible to a desktop-only,
// mechanical sweep — `ux-audit` reported 0 blocking findings at both themes AND at 390px.
//
//   RESPONSIVE  At 390px the longest tag needs 252px of a 187px slot, so
//               `operational-runbooks-and-checklists` renders as `operational-runbooks-and-che…`
//               with `title: null`. The DOM text is complete, so assistive tech was the only reader
//               getting the whole name. At 1440px nothing truncates (1041px available).
//
//   COPY        The hint named right-click only — while `ui/motion/ContextMenu` has carried a
//               keyboard route since the cycle whose comment reads "🔴 THE MENU WAS POINTER-ONLY".
//               Verified here: Tab lands on a row's Rename button and Shift+F10 there opens the same
//               menu with every item. So the capability existed and the one sentence describing it
//               said it did not.
//
// 🔑 WHY THESE TWO SHIP TOGETHER. They are one concern at the altitude that matters — the row hiding
// something the user needs: the full name it truncated, and the second route to its own actions.
// Same component, same reader, two sentences of diff.
//
// 🔑 WHY THE HINT IS THE ONLY ONE THAT NEEDED THIS. Six surfaces wrap rows in `ContextMenu` (tasks,
// inbox, triggers, projects, knowledge items, loops, and this one) and this is the ONLY one with a
// user-facing hint at all; the others mention right-click in code comments only. Fixing the sentence
// that exists is not the same as adding five new ones, which would be a copy decision.

const SRC = readFileSync(join(process.cwd(), 'src/pages/knowledge/TagManager.tsx'), 'utf8')
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

describe('a truncated tag name is still recoverable', () => {
  it('the name carries its full value in a title', () => {
    expect(CODE).toMatch(/className="min-w-0 flex-1 truncate[^"]*"[^>]*title=\{tag\.name\}/)
  })

  it('and it is still the element that truncates — the pair is the point', () => {
    // A title on a non-truncating label would be noise; a truncating label without one loses data.
    expect(CODE, 'still truncates').toMatch(/truncate/)
  })

  it('matches how the rest of the app labels a truncating span', () => {
    // Six precedents (SystemWidget ×2, PlanningWalkthrough, OllamaModelManager, RoutingPanel,
    // ArtifactDeploy) all put the full value in `title` on the truncating element itself.
    const sibling = readFileSync(join(process.cwd(), 'src/ui/SystemWidget.tsx'), 'utf8')
    expect(sibling, 'the idiom this follows still ships').toMatch(/truncate[^"]*"\s+title=\{/)
  })
})

describe('the hint names both routes to the menu', () => {
  it('keeps the pointer gesture and adds the keyboard one', () => {
    expect(CODE).toMatch(/Right-click a tag to nest, merge, or delete it/)
    expect(CODE).toMatch(/Tab to one and press Shift\+F10/)
  })

  it('the keyboard route it now promises is really wired', () => {
    // The promise is only honest because the primitive implements it. If that handler is ever
    // removed, this sentence becomes a lie and this assertion is what says so.
    const primitive = readFileSync(join(process.cwd(), 'src/ui/motion/ContextMenu.tsx'), 'utf8')
    expect(primitive, 'Shift+F10 and the ContextMenu key open it')
      .toMatch(/e\.key === 'ContextMenu' \|\| \(e\.key === 'F10' && e\.shiftKey\)/)
    // And this surface must still route its rows through that primitive for the keydown to reach it.
    expect(CODE, 'the row is wrapped in the primitive').toMatch(/<ContextMenu key=\{tag\.id\} items=\{menu\}>/)
  })

  it('the actions it names are the ones the menu offers — the vacuity floor', () => {
    // A hint promising three verbs against a menu that lost one would pass every assertion above.
    expect(CODE, 'nest').toMatch(/label: `Nest under \$\{o\.name\}`/)
    expect(CODE, 'merge').toMatch(/label: `Merge into \$\{o\.name\}`/)
    expect(CODE, 'delete').toMatch(/label: 'Delete', danger: true/)
  })

  it('the unused-tag sentence is untouched', () => {
    // The diff is an addition, not a rewrite: the second sentence carries a real distinction (an
    // unused tag is KEPT) and re-wording it was not this cycle's business.
    expect(CODE).toMatch(/An unused tag is kept — it stays part of your taxonomy/)
  })
})
