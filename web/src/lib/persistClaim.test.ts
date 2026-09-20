import { describe, expect, it, vi } from 'vitest'
import { durableWorkersHint, persistAvailableFrom, persistToggleCopy } from './persistClaim'

// ── The copy generators cannot promise what the host cannot do ─────────────────────────────────
//
// Issue 545's residue. `persist_available` reached the client and the terminal hint branched on
// it, but the CONTROL stayed enabled and lit on a tmux-less host: `aria-pressed` reported ON for
// a setting the backend gate can only ever answer False to, and a click wrote a flag that did
// nothing. These pin the three-state contract the owner exists to enforce.

describe('persistAvailableFrom', () => {
  it('reads the published fact', () => {
    expect(persistAvailableFrom({ persist_available: true })).toBe(true)
    expect(persistAvailableFrom({ persist_available: false })).toBe(false)
  })

  it('treats an ABSENT field as unavailable, not as permission to promise', () => {
    // 🪤 The failure direction is deliberate. An older gateway does not send the key, and the
    // only safe default is the one that declines to make a claim — defaulting to `true` is the
    // original bug restated as a fallback. The backend publishes the key even on its
    // panel-disabled branch precisely so "absent" never has to mean "unknown".
    expect(persistAvailableFrom({})).toBe(false)
  })
})

describe('persistToggleCopy', () => {
  it('promises survival only when the host confirms it', () => {
    const on = persistToggleCopy(true, true)
    expect(on.hint).toBe('Sessions are tmux-backed, so they survive a restart.')
    expect(on.disabled).toBe(false)
    expect(on.active).toBe(true)

    const off = persistToggleCopy(true, false)
    expect(off.hint).toMatch(/Enabling keeps them alive with tmux/)
    expect(off.disabled).toBe(false)
    expect(off.active).toBe(false)
  })

  it('withholds the promise, the click AND the lit state when tmux is missing', () => {
    // The whole defect in one case: the saved flag is ON and the host cannot honour it.
    const inert = persistToggleCopy(false, true)
    expect(inert.label).toBe('Persistent sessions need tmux')
    expect(inert.hint).toMatch(/on but inert/)
    expect(inert.hint).toMatch(/not installed on this host/)
    expect(inert.disabled).toBe(true)
    // `active` is what becomes `aria-pressed`. Nothing IS on, whatever the flag says.
    expect(inert.active).toBe(false)
    // And no branch of the unavailable case may state the survival promise.
    for (const flag of [true, false]) {
      expect(persistToggleCopy(false, flag).hint).not.toMatch(/they survive a restart/)
    }
  })

  it('makes no claim at all before the host has answered', () => {
    for (const flag of [true, false]) {
      const unknown = persistToggleCopy(null, flag)
      expect(unknown.hint).toMatch(/Checking whether this host can/)
      expect(unknown.disabled).toBe(true)
      expect(unknown.active).toBe(false)
      expect(unknown.hint).not.toMatch(/they survive a restart/)
    }
  })

  it('keeps the enabled-state label an ACTION, and the unavailable label a PRECONDITION', () => {
    // `label` IS the accessible name and the icon-tier tooltip, so it must say what the click
    // does — "Persistent sessions on" does not tell a screen-reader user what happens next.
    expect(persistToggleCopy(true, true).label).toBe('Disable persistent sessions')
    expect(persistToggleCopy(true, false).label).toBe('Enable persistent sessions')
    // 🪤 And it must stay SHORT: `ui/HeaderActions` degrades the header cluster as a whole, so a
    // long label on one control demotes every sibling to icon-only. The longest label here is
    // held within a few characters of the widest sibling it lives beside.
    const longest = Math.max(
      ...[persistToggleCopy(true, true), persistToggleCopy(false, true), persistToggleCopy(null, true)]
        .map((c) => c.label.length),
    )
    expect(longest, 'a fuller sentence belongs in `hint`, not in `label`').toBeLessThanOrEqual(31)
  })
})

describe('durableWorkersHint', () => {
  it('carries the host answer in both directions', () => {
    expect(durableWorkersHint(true)).toMatch(/This host has tmux, so it takes effect/)
    expect(durableWorkersHint(false)).toMatch(/no effect until you install it/)
  })

  it('states the bare requirement while unknown, and never a wrong answer', () => {
    expect(durableWorkersHint(null)).toMatch(/Requires the tmux binary\.$/)
    expect(durableWorkersHint(null)).not.toMatch(/has no effect|takes effect/)
  })

  it('always explains what the setting DOES, whatever the host answer is', () => {
    for (const a of [true, false, null]) {
      expect(durableWorkersHint(a)).toMatch(/outlives the gateway/)
      expect(durableWorkersHint(a)).toMatch(/marks the run resumable/)
    }
  })
})

// ── A capability probe may not take down the surface that asks ─────────────────────────────────

describe('usePersistAvailable', () => {
  it('stays at "unknown" when the probe fails, instead of throwing into the caller', async () => {
    // 🪤 The hook reads a TERMINAL endpoint on behalf of Settings › Agent, a page with nothing
    // else to do with terminals. Measured: with the endpoint absent the call threw SYNCHRONOUSLY
    // inside the effect, React unwound the tree, and ten unrelated assertions about runner
    // honesty failed on a crashed render — for a hint. A failed probe must read as "no answer",
    // which claims nothing (`durableWorkersHint(null)`), and must never surface as an error.
    vi.resetModules()
    vi.doMock('./api', () => ({
      api: { terminalSessions: () => { throw new Error('no such method') } },
    }))
    const { usePersistAvailable: hook } = await import('./persistClaim')
    const { renderHook } = await import('@testing-library/react')
    const { result, unmount } = renderHook(() => hook())
    expect(result.current).toBeNull()
    await Promise.resolve()
    expect(result.current, 'a failed probe claims nothing').toBeNull()
    unmount()
    vi.doUnmock('./api')
    vi.resetModules()
  })
})
