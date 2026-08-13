import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { TileButton } from '../ui/TileButton'
import { IconButton } from '../ui/IconButton'
import { Trash2 } from 'lucide-react'

// ── What Chrome COMPUTES as a control's name, which is not what the source says ─────────────────
//
// Four cycles (136, 137, 139, 140) shipped changes whose entire effect is announcement, and every one
// was verified by reading ATTRIBUTES off the DOM. This cycle read the layer below: Chrome's computed
// accessibility tree over CDP (`Accessibility.getFullAXTree`), 17 routes, **1260 exposed interactive
// nodes**. Attributes are the input; the AX tree is the output, and the two disagree in both
// directions.
//
// It confirmed the four cycles (23 dashboard row actions → 22 distinct computed names; the context
// menu's focus really lands on a `menuitem`; the typeahead editor really carries an activedescendant
// RELATION, not just an attribute) and **found two defects that attribute-reading cannot see**:
//
//   #/artifacts       5 tiles whose computed name was **438-695 characters** of their own rendered
//                     markdown preview — heading `#`, `**` emphasis and blockquote `>` included.
//                     Source looks fine: the tile passes `title={art.name}`. A button with CONTENT
//                     takes its name from the content, and `title` loses to it (cycle 139's lesson,
//                     one layer deeper).
//   #/notifications   83× "Investigate in chat", 83× "Delete", 81× "Mark unread" — three names for
//                     **247 controls**, on the one list surface cycle 139's DOM census undercounted
//                     (its row grouping keyed on 40 characters of the row's text, so it reported
//                     "none"). The AX tree does the grouping properly.
//
// 🔑 ONE RULE, TWO FAILURE MODES: a row control's name must be DISTINGUISHING **and** BOUNDED. Too
// little is 83 rows sharing a verb; too much is a paragraph. Measured trade-off at three caps:
//
//   cap      worst duplicate on #/notifications      interactive names >80ch app-wide
//   (none)   ×83 → ×3                                50 → 219
//   60       ×3                                      50 → 114
//   **55**   ×3                                      50 → **45**
//
// 55 beats the baseline on BOTH metrics. 🪤 The first version of this shipped at 90 and traded three
// duplicate names for 169 new over-long ones — caught only by re-running the sweep, and the reason the
// comment in NotificationsPage carries the table rather than a claim.
//
// 🪤 THE FIRST ATTEMPT AT THE FIX DID NOT FIX IT EITHER: naming from `n.title` alone left 35×
// "…: Refine a skill" and 26× "…: Loop progress", because the title is a KIND on this surface, not an
// identity. Second time in three cycles that the obvious subject field was not the distinguishing one
// (cycle 140's was `e.title` on inbox proposals). **Re-measure after composing a name.**

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const codeOf = (rel: string) => read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('a tile whose content is a document needs an explicit name', () => {
  it('TileButton takes ariaLabel, and it wins over the content', () => {
    render(
      <TileButton title="Design Notes" ariaLabel="Design Notes">
        <div># Verdant Hollow — Design Notes ## Core loop - Explore procedurally-generated forests…</div>
      </TileButton>,
    )
    const b = screen.getByRole('button', { name: 'Design Notes' })
    expect(b.getAttribute('title')).toBe('Design Notes')
  })

  it('without it the name IS the content — the defect, pinned', () => {
    render(<TileButton title="Design Notes"><div># Verdant Hollow — Design Notes ## Core loop</div></TileButton>)
    const b = screen.getByRole('button', { name: /Verdant Hollow/ })
    expect(b, 'title does not win over content').toBeTruthy()
  })

  it('the artifact card passes the artifact name', () => {
    // Cycle 153 removed the `active={active}` that used to sit between `onClick` and `title`: it was
    // threaded from a hard-coded `activeSlug={null}`, so it could never be true. The assertion this
    // test exists for — the tile carries an explicit `ariaLabel` instead of 438-695 characters of
    // markdown preview — is unchanged.
    expect(codeOf('pages/artifacts/ArtifactCard.tsx'))
      .toMatch(/<TileButton onClick=\{\(\) => onOpen\(art\)\} title=\{art\.name\} ariaLabel=\{art\.name\}/)
    expect(codeOf('pages/artifacts/ArtifactCard.tsx'), 'and it claims no selection state')
      .not.toMatch(/active=\{active\}/)
  })
})

describe('an icon button can carry a row name without a paragraph tooltip', () => {
  it('IconButton takes a title override, like SquareIconButton already did', () => {
    render(<IconButton icon={Trash2} label="Delete: Loop progress — cycle 4 finished" title="Delete" onClick={vi.fn()} />)
    const b = screen.getByRole('button', { name: 'Delete: Loop progress — cycle 4 finished' })
    expect(b.getAttribute('title'), 'the hover hint stays the bare verb').toBe('Delete')
  })

  it('defaults the tooltip to the label when no override is given', () => {
    render(<IconButton icon={Trash2} label="Delete" onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Delete' }).getAttribute('title')).toBe('Delete')
  })

  it('the override composes with disabledReason rather than replacing it', () => {
    render(<IconButton icon={Trash2} label="Delete: a row" title="Delete" disabled disabledReason="Nothing selected" onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Delete: a row' }).getAttribute('title')).toBe('Delete — Nothing selected')
  })
})

describe("the notification row actions name their row, and stay bounded", () => {
  const code = codeOf('pages/notifications/NotificationsPage.tsx')

  it('all four actions name the row through the shared helper', () => {
    // Cycle 142 moved the composition into `lib/rowSubject` (one rule, one number, two surfaces), so
    // this asserts the call rather than a local copy of the join.
    for (const verb of ['Investigate in chat', 'Mark unread', 'Mark read', 'Delete']) {
      expect(code, `${verb} must name its row`)
        .toMatch(new RegExp(`\`${verb}: \\$\\{rowSubject\\(\\[n\\.title, firstLine`))
    }
  })

  it('the composition and its cap live in the shared helper, not here', () => {
    expect(code).toMatch(/rowSubject\(\[n\.title, firstLine\(n\.body \?\? ''\)\]\)/)
    expect(code, 'a local re-implementation is the drift this closed').not.toMatch(/function rowName/)
    expect(code, 'and its number with it').not.toMatch(/full\.length > 55/)
  })

  it('the tooltips stay the bare verbs', () => {
    expect(code).toMatch(/title="Mark unread"/)
    expect(code).toMatch(/title="Mark read"/)
    expect(code).toMatch(/title="Delete"/)
  })

  it('InvestigateButton keeps its bare-verb default for single-instance use', () => {
    const inv = codeOf('ui/InvestigateButton.tsx')
    expect(inv).toMatch(/label=\{label \?\? 'Investigate in chat'\}/)
    expect(inv).toMatch(/title="Investigate in chat"/)
  })
})
