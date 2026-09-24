/**
 * #558, behaviourally — the cockpit actually REACHES its gone state.
 *
 * The sibling rail in `deletedLoopIsReported.test.tsx` pins the wiring by source scan, which is
 * necessary (the defect was the *absence* of a reader) but not sufficient: a scan passes even if the
 * effect is unreachable. This one renders the real component, delivers a real `deleted` lifecycle
 * event through the stream seam, and asserts the user-visible outcome. It earned its keep
 * immediately: it caught the gone screen being nested inside `if (!c)`, which made every correctly
 * wired signal unobservable for a loop that had already loaded.
 *
 * 🪤 WHY THE POLL PATH IS NOT ALSO TESTED HERE. A behavioural version needs `vi.useFakeTimers()` to
 * reach the 30s fallback poll, and that file — on its own, restored in both a `finally` and an
 * `afterEach` — reddened THREE unrelated tests (`productTour`, `fieldHintCounts`, `evalsRoundTrip`)
 * in the full suite while passing in isolation. Measured: 610 files green, 613 files 3 failures,
 * and green again with only the two non-timer files. So it perturbed the run rather than finding
 * anything, and it was dropped. The poll's 404 branch is pinned structurally in the sibling rail
 * instead, and mutation-tested there: collapsing the 404 back into a plain `null`, and the opposite
 * error of treating ANY null as gone, are both caught. Do not re-add fake timers here without
 * re-measuring the full suite.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, act as domAct } from '@testing-library/react'
import type { RunLifecycleEvent } from './useRunStream'

const LOOP = {
  id: 'l-1', name: 'Watch me', kind: 'goal', status: 'running',
  total_cycles: 1, created_at: '2026-01-01T00:00:00Z',
}

/** The lifecycle callback the page hands to `useRunStream`, captured so a test can fire an event. */
let fire: ((event: RunLifecycleEvent, data: unknown) => void) | null = null

function mockDeps(uLoop: () => Promise<unknown>, uLoopAction?: unknown) {
  vi.doMock('./useRunStream', () => ({
    useRunStream: (_id: string, _enabled: boolean, handlers: { onLifecycle: typeof fire }) => {
      fire = handlers.onLifecycle
      return { connected: true }
    },
  }))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        uLoop,
        uLoopAction: uLoopAction ?? vi.fn(() => Promise.resolve(LOOP)),
        uLoopReport: () => Promise.resolve({ report: '', log: '' }),
        artifacts: () => Promise.resolve([]),
        task: () => Promise.resolve(null),
        project: () => Promise.resolve({ name: 'P' }),
        uLoopNudges: () => Promise.resolve([]),
      },
    }
  })
}

async function mount() {
  const { LoopCockpitPage } = await import('./LoopCockpitPage')
  render(<LoopCockpitPage id="l-1" onBack={() => {}} query={{}} setQuery={() => {}} />)
}

const goneShown = () =>
  waitFor(() => expect(screen.getByText(/doesn’t exist|does not exist/i)).toBeTruthy(), { timeout: 3000 })

// 🪤 `cleanup()` runs in BOTH hooks on purpose. These tests each mount the same page, and `screen`
// queries the whole document — a tree left over from the previous test made `getByText('Watch me')`
// resolve against the OLD render, so the next test advanced its timers before its own component had
// loaded. Isolated, that test passed; in file order it failed.
beforeEach(() => { cleanup(); vi.resetModules(); fire = null; sessionStorage.clear() })
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('a loop deleted while its cockpit is open', () => {
  it('flips to the gone state on the `deleted` lifecycle event', async () => {
    // The loop loads FINE first — that is the case the old code could never recover from, because
    // the only route to not-found was "never loaded at all".
    mockDeps(() => Promise.resolve(LOOP))
    await mount()
    await waitFor(() => expect(screen.getByText('Watch me')).toBeTruthy())
    expect(fire, 'the page must have subscribed').not.toBeNull()

    domAct(() => fire!('deleted', {}))
    await goneShown()
  })

})
