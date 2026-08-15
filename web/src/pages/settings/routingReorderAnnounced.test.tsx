import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, act, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { RoutingPanel } from './RoutingPanel'

// ── Reordering the routing policy must be announced, not only shown ─────────────────────────────
//
// The reorder buttons were already built carefully: each carries `aria-label="Move <ref>
// earlier|later"`, the ends are `aria-disabled` with a reason via `unavailableWhen`, the arrow icons
// are `aria-hidden`, and the list is a real `<ol>` with visible position numbers. What was missing is
// the outcome: pressing the button swaps two rows and nothing says so. The position numbers are not
// in any focused control's accessible name, and the section's only live region was the READ-FAILURE
// fallback — so a successful move produced no output at all (WCAG 4.1.3).
//
// Verified on a live gateway before this change: `#/settings/routing?uc=reasoning` renders the `<ol>`
// with two "Move …" buttons, and the only `role="status"` / `role="alert"` regions on the page belong
// to the app shell (both empty) — nothing in the section reports a move.
//
// Two details are load-bearing:
//
//   · The announcement is gated on the write SUCCEEDING. `save()` swallows its error to render
//     `note` instead of rejecting, so its promise settles either way; it now returns a boolean and
//     `move` announces only on `true`. Announcing a reorder that the server rejected would be worse
//     than silence, and the failure path already interrupts via `FieldError`'s `role="alert"`.
//   · The region is always mounted and empty at rest, for the same reason `ResultAnnouncement`
//     records: a live region created at the moment its content appears is not reliably observed.

const ROWS = [
  {
    use_case: 'reasoning', mode: 'off', pin: '',
    candidates: [{ ref: 'local:qwen', local: true }, { ref: 'cloud:opus', local: false }],
    classes: { short_chat: { order: ['local:qwen', 'cloud:opus'], basis: { source: 'manual' } } },
  },
]

const setRoutingPolicy = vi.fn((_body: unknown) => Promise.resolve({}))
const routingPolicy = vi.fn(() => Promise.resolve({ enabled: true, use_cases: ROWS }))

vi.mock('../../lib/api', () => ({
  api: {
    routingPolicy: () => routingPolicy(),
    setRoutingPolicy: (b: unknown) => setRoutingPolicy(b as never),
    modelsTelemetry: () => Promise.resolve({ rows: [] }),
  },
}))
vi.mock('../../lib/useCachedData', () => ({
  useCachedData: (_k: string, fn: () => Promise<unknown>) => {
    const [d, setD] = require('react').useState(undefined)
    require('react').useEffect(() => { void fn().then(setD) }, [])
    return { data: d, refresh: () => {} }
  },
}))

function renderPanel() {
  return render(<RoutingPanel query={{ uc: 'reasoning', qc: 'short_chat' }} setQuery={() => {}} />)
}

describe('a routing reorder is announced', () => {
  beforeEach(() => { setRoutingPolicy.mockClear(); setRoutingPolicy.mockImplementation(() => Promise.resolve({})) })

  it('the section mounts a polite status region, empty at rest', async () => {
    const { container } = renderPanel()
    await waitFor(() => expect(screen.getAllByRole('button', { name: /^Move / }).length).toBeGreaterThan(0))
    const regions = [...container.querySelectorAll('[role="status"][aria-live="polite"]')]
    const sr = regions.find((r) => r.className.includes('sr-only'))
    expect(sr, 'an sr-only polite region must exist before any move').toBeTruthy()
    expect(sr!.textContent).toBe('')
  })

  it('a successful move announces the ref AND its new position', async () => {
    const { container } = renderPanel()
    const later = await waitFor(() => screen.getByRole('button', { name: 'Move local:qwen later' }))
    await act(async () => { later.click() })
    await waitFor(() => {
      const sr = [...container.querySelectorAll('[role="status"][aria-live="polite"]')]
        .find((r) => r.className.includes('sr-only'))
      // "position 2 of 2" — the number is the point: "moved later" alone does not say where it landed.
      expect(sr!.textContent).toBe('local:qwen moved to position 2 of 2')
    })
    expect(setRoutingPolicy).toHaveBeenCalledWith(
      expect.objectContaining({ use_case: 'reasoning', query_class: 'short_chat', order: ['cloud:opus', 'local:qwen'] }),
    )
  })

  it('a FAILED move announces nothing', async () => {
    // The whole reason `save` returns a boolean. The error path has its own role="alert".
    setRoutingPolicy.mockImplementation(() => Promise.reject(new Error('nope')))
    const { container } = renderPanel()
    const later = await waitFor(() => screen.getByRole('button', { name: 'Move local:qwen later' }))
    await act(async () => { later.click() })
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    const sr = [...container.querySelectorAll('[role="status"][aria-live="polite"]')]
      .find((r) => r.className.includes('sr-only'))
    expect(sr!.textContent, 'a rejected write must not claim the move happened').toBe('')
  })

  it('save reports success, so the announcement cannot be inferred from settling', () => {
    // Guard the mechanism, not just the outcome: if `save` ever stops returning a boolean, the
    // `.then(ok => ...)` gate silently becomes "announce always" again.
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/RoutingPanel.tsx'), 'utf8')
    expect(src).toMatch(/Promise<boolean>/)
    expect(src).toMatch(/\.then\(\(ok\) => \{ if \(ok\)/)
  })
})
