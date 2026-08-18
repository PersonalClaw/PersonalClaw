import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── An indicator that only appears when something is wrong must appear when it cannot tell ───────
//
// `DegradedChip` renders nothing when every surface has a model. That is right — and it is exactly
// why `catch(() => {})` on its own read was not: `surfaces` stayed `null`, `down.length === 0`, and
// the chip returned null. **The absence of the chip is a claim, and a failed read made that claim.**
//
// Measured with only `/api/resilience/degraded` at 500 and the rest of the gateway healthy (dev
// gateway + the failure proxy):
//
//   • SEVEN surfaces genuinely degraded
//   • no chip at all — `text=/degraded/i` count 0
//   • and the sibling connectivity indicator in the SAME header corner reading "Gateway connected"
//
// So the mitigation was not merely missing; it was replaced by active reassurance. That measurement
// is the reason this is a defect and not a tolerable gap: nothing else on the shell contradicts it.
//
// 🔑 The fix is asymmetric on purpose. A LATER failure keeps the last good answer (the poll only
// assigns on success), so a blip mid-session does not flicker the chip. The gap was the COLD start:
// a first poll that fails has nothing to fall back on. Hence `surfaces === null && unread`.
//
// 🪤 The unknown state must not claim a fault. The surfaces may all be fine; asserting a problem we
// have not measured is the same error in reverse, so the copy says what it cannot tell and the
// popover explains rather than listing an empty set.

const boom = () => Promise.reject(new Error('resilience read failed'))
const degraded = { surfaces: [{ surface: 'chat', available: false, backlog: 2, floor: 'keyword search', use_case: 'chat' }] }
const healthy = { surfaces: [{ surface: 'chat', available: true, backlog: 0, floor: '', use_case: 'chat' }] }

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: { degraded: () => Promise.resolve(healthy), ...over },
  }))
}

async function mount() {
  const { DegradedChip } = await import('./DegradedChip')
  render(<DegradedChip />)
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the degraded chip cannot stay silent about a check it could not read', () => {
  it('says the status is unknown when the read has never answered', async () => {
    mockApi({ degraded: boom })
    await mount()
    const chip = await waitFor(() => screen.getByRole('button', { name: /Status unknown/i }))
    expect(chip.getAttribute('title'), 'and says what that means').toMatch(/could not be read/i)
    // Deliberately NOT a claim of failure — the surfaces may be fine.
    expect(chip.textContent, 'no invented fault').not.toMatch(/degraded/i)
  })

  it('stays silent when every surface really does have a model', async () => {
    mockApi({})
    await mount()
    // The chip's whole design is to be absent when there is nothing to say. Pinned so the fix
    // cannot turn into a permanent header ornament.
    await waitFor(() => expect(screen.queryByRole('button')).toBeNull())
  })

  it('still reports a real degradation, with its count', async () => {
    mockApi({ degraded: () => Promise.resolve(degraded) })
    await mount()
    await waitFor(() => expect(screen.getByRole('button', { name: /Chat degraded/i })).toBeInTheDocument())
    expect(screen.queryByText(/Status unknown/), 'a measured fault is not "unknown"').toBeNull()
  })

  it('the popover explains the unknown instead of opening on an empty list', async () => {
    mockApi({ degraded: boom })
    await mount()
    fireEvent.click(await waitFor(() => screen.getByRole('button', { name: /Status unknown/i })))
    const dialog = await waitFor(() => screen.getByRole('dialog'))
    expect(dialog.textContent).toMatch(/Could not read the check/i)
    expect(dialog.textContent, 'says what it cannot tell').toMatch(/cannot say whether any surface/i)
    expect(dialog.textContent, 'and that it self-heals').toMatch(/clear itself when the check responds/i)
  })
})

describe('the source keeps the two states apart', () => {
  const code = readFileSync(join(process.cwd(), 'src/ui/DegradedChip.tsx'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('the read no longer discards its rejection', () => {
    // 🪤 Asserted on the whole poll body, not as a prefix of `api.degraded()` — a prefix match happily
    // accepts a re-appended `.catch(() => {})` (that mutation slipped in the previous cycle).
    const at = code.indexOf('api.degraded()')
    expect(at, 'the read must still be here').toBeGreaterThan(-1)
    const poll = code.slice(at, code.indexOf('}, 20000)', at))
    expect(poll, 'the rejection must be recorded').toMatch(/\.catch\(\(\) => setUnread\(true\)\)/)
    expect(poll, 'and not dropped').not.toMatch(/\.catch\(\(\) => \{\s*\}\)/)
  })

  it('unknown requires BOTH never-answered and a failing read', () => {
    // Either half alone is wrong: `unread` on its own would flicker over a good last answer, and
    // `surfaces === null` on its own is also the pre-first-poll state, which must stay silent.
    expect(code).toMatch(/const unknown = surfaces === null && unread/)
    expect(code, 'and a successful read clears it').toMatch(/setSurfaces\(r\.surfaces\); setUnread\(false\)/)
  })

  it('an empty-but-known result still renders nothing', () => {
    expect(code).toMatch(/if \(down\.length === 0 && !unknown\) return null/)
  })
})
