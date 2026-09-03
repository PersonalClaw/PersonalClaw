import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── Discover's area headings skipped a rung ───────────────────────────────────────────────────────
//
// The page renders a `PageTitle` (an h1) and then five area sections. Each section's heading was an
// `<h3>`, and the page has no h2 at all, so the outline read:
//
//     h1 "Discover"  →  h3 "Talk to it"  h3 "Let it work"  h3 "Stay organized"  …
//
// A five-times-repeated `h1 → h3` skip: WCAG 1.3.1, reported by `ux-audit` at BOTH themes and at
// 390px, so it is not a viewport or theme artifact.
//
// 🔑 THE RUNG IS ALREADY SETTLED ELSEWHERE, which is what makes this drift rather than a taste call.
// Driven outlines on a populated home:
//
//     #/dashboard         h1 + nine  h2   ("Needs you", "Active work", "Tasks", …)
//     #/knowledge         h1 + h2
//     #/inbox             h1 + h2
//     #/settings/sources  h1 + four  h2   ("Polling", "Limits", "Artifacts", …)
//     #/discover          h1 + five  h3   ← the outlier
//
// And the 14 other `<h3>`s in the tree are NOT the same job: they sit inside panels and drawers
// (`WorkspacePanel`, `NodeInspectorDrawer`, `OutboxPanel`, `IntrospectPanel`, `ProvidersPanel`,
// `DesignPanel`'s token groups, `CodeCockpitPage`) beneath an h2, where h3 is the correct rung — plus
// `ui/Markdown`, which maps an authored `###` to the tag that content asked for. This was the only
// page-level section heading using it, and the only one the audit flags.
//
// 🪤 My first census of those h3 files was truncated by a `head -12` and so missed `ui/Markdown`. The
// rail's own list is what caught it — the same shape as last cycle, where the sweep found two sites my
// grep had missed. A census that scrolls off the end of a pager is not a census.
//
// 🔑 NOTHING MOVES VISUALLY. The type comes from `data-type="label-l"`, never from the tag name, so the
// captures are pixel-identical at both themes and at 390px. That is the expected result, not a missing
// screenshot.
//
// 🪤 THE OTHER THREE "heading order" FINDINGS FROM THE SAME SWEEP ARE NOT THIS BUG, measured rather
// than assumed:
//   #/settings/sources   a clean h1 + 4×h2 outline — the finding was a PHANTOM of a 41-surface batch
//                        run and does not reproduce individually (third batch heading-order phantom).
//   #/artifacts/…        NO headings at all: on this seed the artifact fails to load and the page is
//                        an error state, so there is no outline to order.
//   #/app/native-knowledge  NO headings at all: the app-host shell embeds a third-party app that owns
//                        its own headings. Defensible; not this family.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const strip = (s: string) => s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')

describe("Discover's area headings sit one rung under the page title", () => {
  const PAGE = strip(read('pages/discover/DiscoverPage.tsx'))

  it('the area heading is an h2', () => {
    expect(PAGE).toMatch(/<h2 data-type="label-l" className="text-on-surface-var">\{group\.area\}<\/h2>/)
  })

  it('no h3 remains on the page', () => {
    expect(PAGE, 'a page-level section may not skip to h3').not.toMatch(/<h3[\s>]/)
  })

  it('the page still renders exactly one h1, through PageTitle', () => {
    // The floor for "h2 is one rung down". If the page ever loses its PageTitle, h2 becomes the skip
    // and this reasoning needs redoing rather than silently passing.
    expect(PAGE).toMatch(/<PageTitle/)
    expect(PAGE, 'and no hand-rolled h1 competing with it').not.toMatch(/<h1[\s>]/)
  })

  it('the type still comes from data-type, so the tag change is invisible', () => {
    // If the heading ever takes its size from the tag, swapping the tag becomes a visual change and
    // the "pixel-identical" claim in the PR stops being true.
    expect(PAGE).toMatch(/data-type="label-l"/)
  })

  it('the h3s that remain in the tree are all panel-level or markdown — the scope claim', () => {
    // The vacuity floor for "only Discover was drift". If a page-level file starts using h3 again this
    // count moves and the classification above must be re-done.
    const walk = (dir: string, out: string[] = []): string[] => {
      for (const name of readdirSync(dir)) {
        const abs = join(dir, name)
        if (statSync(abs).isDirectory()) walk(abs, out)
        else if (/\.tsx$/.test(name) && !name.includes('.test.')) out.push(abs)
      }
      return out
    }
    const withH3 = walk(SRC)
      .filter((abs) => /<h3[\s>]/.test(strip(readFileSync(abs, 'utf8'))))
      .map((abs) => abs.replace(SRC + '/', ''))
    // 2026-08-19, and the classification was re-done as this comment requires:
    //   · `settings/ProvidersPanel` LEFT the list — it reported `h1 → h3` (axe heading-order) and now
    //     renders its group headings through `settingsUI`'s `Section` (an h2), so it writes no heading
    //     tag of its own at all.
    //   · `settings/VoicePanel` JOINED it — its "Learned corrections" sub-heading was an `h4` under a
    //     `Section` h2 (measured live: `H1 > H2 > H2 > H2 > H2 > H4`) and is now the h3 that nesting
    //     actually calls for. Panel-level, correctly nested: the classification above still holds.
    // 2026-09-03, re-done again:
    //   · `settings/DesignPanel` LEFT the list — its palette-group heading now renders through
    //     `Eyebrow as="h3"` (the caption-tier heading treatment), so the file writes no literal
    //     `<h3` tag; the rendered outline still carries the same h3 rung. Panel-level, correctly
    //     nested: the classification above still holds.
    expect(withH3.sort(), 'files still using h3').toEqual([
      'pages/code/CodeCockpitPage.tsx',
      'pages/settings/VoicePanel.tsx',
      'pages/workflows/IntrospectPanel.tsx',
      'pages/workflows/NodeInspectorDrawer.tsx',
      'pages/workflows/OutboxPanel.tsx',
      'pages/workflows/WorkspacePanel.tsx',
      // `ui/Markdown` is the odd one and is CORRECT: it maps a `###` in authored content to an h3,
      // which is the tag that source asked for. It is not a page chrome decision at all.
      'ui/Markdown.tsx',
    ])
  })
})
