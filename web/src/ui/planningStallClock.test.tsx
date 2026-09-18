/**
 * Issue 488 — the stall clock is server-sourced, so a dead planner offers Retry on first paint.
 *
 * The quiet clock was `useRef(Date.now())`, seeded at component MOUNT, so `quietMs` measured "how
 * long since this page loaded" rather than "how long since the session progressed". Measured
 * against a live gateway on a session dead 5.6 hours: three live spinners, no Retry, and Retry
 * appeared only at 190s of page UPTIME — then vanished on reload as the countdown restarted.
 *
 * The opposite failure matters just as much and is asserted below: Retry KILLS an in-flight pass, so
 * a healthy session that has merely been planning for a while must not be offered it. That is why
 * `created_at` alone could not fix this and a real `updated_at` stamp was added server-side.
 *
 * 🪤 EVERY assertion here DRIVES the clock instead of polling for a hopeful frame. That is not
 * ceremony. An earlier draft of this fix used a bare `waitFor(retryOffered)`, went green, and was
 * still broken in a real browser: `waitFor` polls, so it caught the transient frame between the
 * session poll resolving and the effect that (wrongly) re-stamped the clock. Advancing fake time
 * past the point where a re-stamped clock would read "quiet for 30s" is what makes a reset
 * detectable at all — a reset clock cannot survive `advance(30s)` and still say "stalled".
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, act } from '@testing-library/react'
import { PlanningWalkthrough, type WalkthroughConfig } from './PlanningWalkthrough'
import type { PlanSession } from '../lib/api'

const HOUR_S = 3600
const STALL_MS = 180_000
const nowS = () => Date.now() / 1000

/** A session whose current step is mid-flight — the shape that renders spinners. */
function session(over: Partial<PlanSession> = {}): PlanSession {
  return {
    project_id: 'l-1',
    created_at: nowS() - 6 * HOUR_S,
    steps: [{ id: 'step-0', kind: 'brief', title: 'Brief', status: 'running' } as never],
    ...over,
  } as PlanSession
}

function cfg(sess: PlanSession | null): WalkthroughConfig {
  return {
    planSessionKey: (id: string) => `goal-plan-${id}`,
    api: {
      getSession: () => Promise.resolve(sess),
      start: vi.fn(() => Promise.resolve({})),
      approve: vi.fn(() => Promise.resolve({})),
      comment: vi.fn(() => Promise.resolve({})),
      edit: vi.fn(() => Promise.resolve({ session: sess as PlanSession })),
      isReady: () => Promise.resolve(false),
      retry: vi.fn(() => Promise.resolve({})),
    },
    copy: {
      subtitle: 'Planning',
      activityLabel: 'Investigation',
      activityEmpty: 'Nothing yet.',
      cancel: 'Back',
    },
    renderArtifact: () => null,
  } as unknown as WalkthroughConfig
}

const mount = (sess: PlanSession | null) =>
  render(<PlanningWalkthrough id="l-1" cfg={cfg(sess)} onReady={() => {}} onBack={() => {}} />)

const retryOffered = () => screen.queryAllByText(/retry/i).length > 0
const shows = (re: RegExp) => screen.queryAllByText(re).length > 0
/** Every element currently running the spin keyframe. This is the issue's OTHER half: the
 *  complaint was not only "no Retry" but that spinners claimed work was in flight. Counting the
 *  class is exactly what the browser measurement counted, so rail and manual check agree. */
const liveSpinners = () => document.querySelectorAll('.animate-spin').length

/** Let the session poll land, its effects flush, and the derived clock tick — then read the
 *  SETTLED verdict. 30s is chosen to be well under the 180s threshold: any code path that treats
 *  the page load as progress reads "quiet for 30s" here and cannot report stalled. */
const settle = (ms = 30_000) => act(() => vi.advanceTimersByTimeAsync(ms))

beforeEach(() => {
  cleanup()
  vi.clearAllMocks()
  // jsdom implements no scrolling, and the component scrolls its activity feed on update.
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
  vi.useFakeTimers({ shouldAdvanceTime: true })
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('a session that died hours ago', () => {
  it('offers Retry once loaded, and KEEPS offering it', async () => {
    // 🔑 The reported case: dead 5.6h, spinners claiming work, Retry never reachable. The clock is
    // the server's last-progress stamp, so the verdict is right as soon as the session arrives —
    // and survives the poll cycles that follow, which is where the old code lost it.
    mount(session({ updated_at: nowS() - 6 * HOUR_S }))
    await settle()
    expect(retryOffered()).toBe(true)
    await settle(60_000)
    expect(retryOffered(), 'a later poll of unchanged state must not re-arm the countdown').toBe(true)
  })

  it('reads as paused rather than working', async () => {
    mount(session({ updated_at: nowS() - 6 * HOUR_S }))
    await settle()
    expect(shows(/planning paused/i)).toBe(true)
  })

  it('falls back to created_at when updated_at is absent', async () => {
    // A session written before the field existed. The backend backfills on read, so this is
    // belt-and-braces — but the frontend must not depend on the backfill having happened.
    const s = session()
    delete (s as { updated_at?: number }).updated_at
    mount(s)
    await settle()
    expect(retryOffered()).toBe(true)
  })

  it('stops claiming work is in flight — NO live spinner anywhere', async () => {
    // 🔑 The issue's other half, and it survives a correct `stalled` verdict only if ALL the
    // spinner sites honour it. The header and the artifact gate already did; the steps rail did
    // not — it keyed purely off the step's persisted `running` status, which is exactly the state
    // a DIED pass leaves behind. Measured in a real browser at three spinners before this fix.
    mount(session({ updated_at: nowS() - 6 * HOUR_S }))
    await settle()
    expect(retryOffered()).toBe(true)
    expect(liveSpinners(), 'a paused planner must not animate a spinner').toBe(0)
  })

  it('still spins while the planner IS alive', async () => {
    // The complement, so the assertion above cannot be satisfied by deleting the spinner.
    mount(session({ updated_at: nowS() - 10 }))
    await settle()
    expect(shows(/drafting this step/i)).toBe(true)
    expect(liveSpinners(), 'a live planner must still show motion').toBeGreaterThan(0)
  })
})

describe('a session that is actually working', () => {
  it('does NOT offer Retry just because it started a while ago', async () => {
    // 🔑 THE OPPOSITE FAILURE, and why `created_at` could not be the clock: this session began
    // 6 hours ago and progressed 10 seconds ago. Offering Retry here would kill live work.
    mount(session({ created_at: nowS() - 6 * HOUR_S, updated_at: nowS() - 10 }))
    await settle()
    expect(retryOffered(), 'Retry on a progressing session kills an in-flight pass').toBe(false)
  })

  it('does not offer Retry to a brand-new session', async () => {
    const t = nowS()
    mount(session({ created_at: t, updated_at: t }))
    await settle()
    expect(retryOffered()).toBe(false)
  })

  it('DOES offer Retry once that same session goes quiet past the threshold', async () => {
    // The boundary from the healthy side: nothing about the fix may pin a live-looking session at
    // not-stalled forever. Same session, same stamp, only time passes.
    mount(session({ created_at: nowS() - 6 * HOUR_S, updated_at: nowS() - 10 }))
    await settle()
    expect(retryOffered()).toBe(false)
    await settle(STALL_MS)
    expect(retryOffered()).toBe(true)
  })
})

describe('the page load itself is not progress', () => {
  it('a session ARRIVING from the poll does not re-arm the countdown', async () => {
    // 🪤 The trap that survived the first draft of this fix and only a real browser caught: the
    // reset effect was gated on "not the first run", but the first run happens while `session` is
    // still null. The poll's arrival then looked like a change — `sessionSig` goes from "[]" to the
    // real steps — and stamped the clock with NOW, so a 5.6h-dead session measured ~0ms of quiet.
    // Mounting a session that is dead by exactly one threshold + a margin makes the re-stamp fatal:
    // a re-armed clock reads 30s here, and 30s is not stalled.
    mount(session({ updated_at: nowS() - (STALL_MS + 60_000) / 1000 }))
    await settle()
    expect(retryOffered(), 'the first successful poll must not count as progress').toBe(true)
  })

  it('a remount of the SAME dead session still reports it stalled', async () => {
    // The reload, expressed as a remount: the old mount seed hid the verdict again on every open.
    const dead = session({ updated_at: nowS() - 6 * HOUR_S })
    mount(dead)
    await settle()
    expect(retryOffered()).toBe(true)
    cleanup()
    mount(dead)
    await settle()
    expect(retryOffered(), 'reopening the page must not restart the countdown').toBe(true)
  })
})

describe('when there is NO session, mount time is the honest floor', () => {
  // 🪤 The one case a server-sourced clock cannot cover, and the trap in making it server-sourced:
  // if `start` never produces a session then nothing has progressed, so `updated_at` and
  // `created_at` are both absent and a plain `max(server, local)` is ZERO — which reads as "quiet
  // for 0ms" and pins the verdict at not-stalled FOREVER. That is worse than the original bug,
  // because the `steps.length === 0` branch's Retry is the only escape from "preparing the steps…".
  it('reaches Retry after the threshold instead of spinning forever', async () => {
    mount(null)
    await settle(1_000)
    expect(retryOffered(), 'not stalled yet — the threshold has not passed').toBe(false)
    await settle(STALL_MS + 10_000)
    expect(retryOffered()).toBe(true)
  })

  it('does not offer Retry immediately on mount', async () => {
    // The floor must be a FLOOR, not "stalled since epoch": a page just opened on a planner that
    // is still spawning has genuinely been quiet for milliseconds.
    mount(null)
    await settle(6_000)
    expect(retryOffered()).toBe(false)
  })
})
