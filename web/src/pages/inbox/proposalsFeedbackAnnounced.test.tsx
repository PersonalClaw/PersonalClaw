import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, act, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ProposalsLens } from './ProposalsLens'
import type { InboxItem } from '../../lib/api'

// ── The Proposals lens's two feedback channels must reach assistive tech ─────────────────────────
//
// Applying a proposal is a write with exactly one visible result: a line under the row saying
// "Applied." or "Not applied — <error>. Still pending." Two things stopped that reaching a screen
// reader, and both had a documented in-repo answer:
//
// 1. The outcome's `role="status"` lived on a CONDITIONALLY MOUNTED div (`{outcome && …}`), so the
//    region was created at the same moment its text appeared. `ResultAnnouncement` records why that
//    fails: "Always MOUNTED (rendered empty when idle) — a live region created at the same moment its
//    content appears is not reliably observed." Measured on a live gateway at `#/inbox?kind=proposal`:
//    35 proposal rows rendered and the lens contributed **zero** live regions at rest (the two
//    `role="status"` and one `role="alert"` present belong to the app shell — they appear on
//    `#/inbox` too, where this lens does not render at all).
// 2. The edit-payload validation error was a plain `<div className="text-error">`, so a rejected edit
//    was silent. `FieldError` (43 uses across 22 files) carries `role="alert"`, which is what an
//    unrequested failure needs, and uses `text-danger` — the token 101 other call sites use, versus
//    `text-error` in only 2.
//
// The fix is ONE node, not a hidden region plus a visible copy: duplicating the sentence would
// announce it twice AND make `getByText(/Not applied/)` ambiguous for INU-7's own row test — which is
// exactly what the full suite caught when I first wrote it that way.

const ITEM = (id: string, title: string): InboxItem => ({
  id, channel: '', channel_name: '', message: title, sender_id: '', sender_name: '',
  item_kind: 'proposal', status: 'pending',
  refs: { proposal: { title, apply: { skill_promotion: { name: 'x' } }, preview: 'p', provenance: 'learning' } },
} as unknown as InboxItem)

const applyInboxProposal = vi.fn((_id: string, _edited?: unknown) => Promise.resolve({ ok: true }))
vi.mock('../../lib/api', () => ({ api: { applyInboxProposal: (i: string, e?: unknown) => applyInboxProposal(i, e) } }))

/** The per-row outcome nodes: one element that is `sr-only` at rest and visible once filled. */
const outcomeNodes = (c: HTMLElement) => [...c.querySelectorAll('[role="status"][aria-live="polite"]')]

describe('proposal apply feedback is announced', () => {
  beforeEach(() => {
    applyInboxProposal.mockClear()
    applyInboxProposal.mockImplementation(() => Promise.resolve({ ok: true }))
  })

  it('every row mounts a polite region, empty before any apply', () => {
    const { container } = render(<ProposalsLens items={[ITEM('a', 'One'), ITEM('b', 'Two')]} onChanged={() => {}} />)
    const regions = outcomeNodes(container)
    expect(regions.length, 'one region per row, present at rest').toBe(2)
    for (const r of regions) {
      expect(r.textContent).toBe('')
      expect(r.className, 'empty costs no layout').toContain('sr-only')
    }
  })

  it('a successful apply announces, and the visible copy is not double-read', async () => {
    const { container } = render(<ProposalsLens items={[ITEM('a', 'One')]} onChanged={() => {}} />)
    const approve = await waitFor(() => screen.getByRole('button', { name: /^Approve$/ }))
    await act(async () => { approve.click() })
    await waitFor(() => expect(outcomeNodes(container)[0].textContent).toBe('Applied.'))
    // The SAME node becomes visible — one sentence, announced and seen, so nothing is read twice
    // and the row's own `getByText` stays unambiguous.
    const node = outcomeNodes(container)[0]
    expect(node.className, 'it stops being sr-only once filled').not.toContain('sr-only')
    // Exactly one node in the row carries the sentence. (Counting all `aria-hidden` would be wrong:
    // every lucide icon legitimately sets it.)
    const carriers = [...container.querySelectorAll('*')]
      .filter((e) => e.textContent === 'Applied.' && e.children.length === 0)
    expect(carriers.length, 'one sentence, one node — nothing read twice').toBe(1)
  })

  it('a failed apply announces the reason and that the item is still pending', async () => {
    applyInboxProposal.mockImplementation(() => Promise.resolve({ ok: false, error: 'dispatcher refused' }))
    const { container } = render(<ProposalsLens items={[ITEM('a', 'One')]} onChanged={() => {}} />)
    const approve = await waitFor(() => screen.getByRole('button', { name: /^Approve$/ }))
    await act(async () => { approve.click() })
    await waitFor(() => expect(outcomeNodes(container)[0].textContent)
      .toBe('Not applied — dispatcher refused. Still pending.'))
  })

  it('the region is not conditionally mounted — the shape that could not announce', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/inbox/ProposalsLens.tsx'), 'utf8')
    // Always mounted with a CONDITIONAL className (sr-only at rest, visible once filled) and text
    // driven by the outcome — not a conditionally mounted element.
    expect(src, 'role + polite live region present').toMatch(/role="status"\s*\n\s*aria-live="polite"/)
    expect(src, 'sr-only only while empty').toMatch(/: 'sr-only'\}/)
    expect(src, 'text driven by the outcome').toMatch(/\{outcome \? \(outcome\.ok \? 'Applied\.'/)
    expect(/\{outcome && \([\s\S]{0,160}role="status"/.test(src),
      'a conditionally mounted region is born with its content and is not reliably observed').toBe(false)
  })

  it('the edit error interrupts through FieldError, not a bare div', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/inbox/ProposalsLens.tsx'), 'utf8')
    expect(src).toMatch(/\{draftError && <FieldError>\{draftError\}<\/FieldError>\}/)
    // Strip comments first: this file's own header quotes `text-error` while explaining the fix, and
    // the comment inside ProposalsLens does too — a raw scan would fail on the documentation.
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
    expect(/text-error/.test(code), 'the vestigial token alias should be gone from CODE').toBe(false)
    // Vacuity guard: FieldError is only the right answer while it actually carries role="alert".
    const forms = readFileSync(join(process.cwd(), 'src/ui/forms.tsx'), 'utf8')
    expect(forms).toMatch(/export function FieldError[\s\S]{0,300}role="alert"/)
  })
})
