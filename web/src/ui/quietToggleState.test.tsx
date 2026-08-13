import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Pencil } from 'lucide-react'
import { QuietButton } from './QuietButton'
import { SquareIconButton } from './SquareIconButton'

// ── The last two state-bearing primitives that kept their state to themselves ─────────────────
//
// Cycle 129 taught `HeaderControl`, `FilterChip` and `IconButton` that `active` means `aria-pressed`.
// Finishing the family:
//
//   `SquareIconButton`  `on` (selected/toggled) → drove the coral tint only.  **2 callers pass it**
//   `QuietButton`       no state prop at all.   **6 of its call sites are disclosures**
//
// 🔑 `QuietButton` GETS `ariaExpanded`, THE NAME `Button` ALREADY USES — not a new spelling for the same
// question. Its six disclosure sites each swap their own label ("View"/"Hide", "Compare versions"/"Close
// compare"), which tells a user what the NEXT click does but not whether the panel is open right now.
//
// 🪤 A CALL-SITE CENSUS CANNOT SEE A FIX MADE IN A PRIMITIVE — the inverse of cycle 128's window bug. After
// cycle 129 the census still reported 34 silent toggles, six of which were already announced through
// `HeaderControl`/`FilterChip`. **Resolve the element before counting it silent**, or a primitive fix looks
// like no fix at all.
//
// Driven, parent worktree vs this one (`grep -c ariaExpanded QuietButton.tsx` = 0 there, 3 here):
//
//   #/settings/providers   `aria-pressed` nodes **0 → 18**   ← the `on` half, measured
//
// ⚠️ THE `QuietButton` HALF WAS **NOT DRIVEN** and it is worth saying so rather than implying six browser
// checks. Its six sites live behind interactions this dev home cannot reach: `WorkflowRunDetail`'s four
// panel toggles need a run whose panels render (the available runs are draft/failed/complete and none
// showed them), and the compare control needs the artifact viewer's version pane. Measured what I could —
// 8 QuietButtons render on the artifact detail and 34 on a run detail, none of them these six — and pinned
// the contract below with render tests instead of claiming a drive I did not do.

describe('QuietButton announces expansion when it is a disclosure', () => {
  it('emits the state when asked', () => {
    render(<QuietButton onClick={vi.fn()} ariaExpanded>View</QuietButton>)
    expect(screen.getByRole('button', { name: 'View' }).getAttribute('aria-expanded')).toBe('true')
  })

  it('emits false when closed, so the state is unambiguous', () => {
    render(<QuietButton onClick={vi.fn()} ariaExpanded={false}>View</QuietButton>)
    expect(screen.getByRole('button', { name: 'View' }).getAttribute('aria-expanded')).toBe('false')
  })

  it('says nothing for a plain quiet action', () => {
    // Download / Source file are not disclosures; `aria-expanded="false"` there would be a false promise.
    render(<QuietButton onClick={vi.fn()}>Download</QuietButton>)
    expect(screen.getByRole('button', { name: 'Download' }).hasAttribute('aria-expanded')).toBe(false)
  })

  it('keeps its title and its quiet geometry', () => {
    render(<QuietButton onClick={vi.fn()} title="Download the findings log">Download</QuietButton>)
    const el = screen.getByRole('button', { name: 'Download' })
    expect(el.getAttribute('title')).toBe('Download the findings log')
    expect(el.className).toMatch(/h-7/)
  })
})

describe('SquareIconButton announces the tint it was already showing', () => {
  it('is pressed when on', () => {
    render(<SquareIconButton icon={Pencil} label="Edit" on onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Edit' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('omits the attribute for a plain icon action', () => {
    render(<SquareIconButton icon={Pencil} label="Delete" onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Delete' }).hasAttribute('aria-pressed')).toBe(false)
  })

  it('keeps the disabled contract cycle 119 gave it', () => {
    render(<SquareIconButton icon={Pencil} label="Edit" disabled disabledReason="Test the connection first" onClick={vi.fn()} />)
    const el = screen.getByRole('button', { name: 'Edit' })
    expect(el.getAttribute('aria-disabled')).toBe('true')
    expect(el.getAttribute('title')).toBe('Edit — Test the connection first')
  })
})

describe('the call sites, classified per site', () => {
  const SRC = join(process.cwd(), 'src')
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

  const DISCLOSURES: [string, string][] = [
    ['pages/ChatPage.tsx', 'open'],
    ['pages/artifacts/ArtifactViewer.tsx', 'comparing'],
    ['pages/workflows/WorkflowRunDetail.tsx', 'workspaceOpen'],
    ['pages/workflows/WorkflowRunDetail.tsx', 'outboxOpen'],
    ['pages/workflows/WorkflowRunDetail.tsx', 'introspectOpen'],
    ['pages/workflows/WorkflowRunDetail.tsx', 'steerOpen'],
  ]

  for (const [rel, state] of DISCLOSURES) {
    it(`${rel.split('/').pop()} passes ariaExpanded={${state}}`, () => {
      expect(read(rel)).toContain(`ariaExpanded={${state}}`)
    })
  }

  it('the two `on` Edit buttons keep passing it', () => {
    expect(read('pages/settings/ModelBackends.tsx')).toMatch(/<SquareIconButton label="Edit"[^\n]*on=\{editing\}/)
    expect(read('pages/settings/MultiInstanceCard.tsx')).toMatch(/<SquareIconButton label="Edit"[^\n]*on=\{editing\}/)
  })

  it('a show/hide-secret button stays silent, because its NAME carries the state', () => {
    // The same ruling as `DiagnosticsPanel`'s pause in cycle 128: when the accessible name flips
    // ("Show" ⇄ "Hide"), the state is already announced and a second channel adds nothing.
    for (const rel of ['pages/settings/ModelBackends.tsx', 'pages/settings/ProviderConfigForm.tsx']) {
      const src = read(rel)
      const at = src.search(/<SquareIconButton label=\{show(Secret)? \? 'Hide' : 'Show'\}/)
      expect(at, `${rel} must still have the name-flipping secret toggle`).toBeGreaterThan(-1)
      expect(src.slice(at, at + 200), 'a name that flips does not need `on` as well').not.toMatch(/\bon=\{/)
    }
  })
})
