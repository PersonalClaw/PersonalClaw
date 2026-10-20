import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { readableErrText } from '../lib/errText'
import { LoadError, EmptyState } from './ListScaffold'

// ── A failed load is not an empty collection ──────────────────────────────────────────────────
//
// `useQuery` returns `{ data, loading, error, refresh }`, and a surface that branches on `data`
// alone sends a failed fetch to the same branch as a genuinely empty result: the user is told "you
// have none" when the truth is "we could not load it", with no retry and nothing announced.
// `LoadError` is the other answer to that condition, kept beside `EmptyState` in the list kit so a
// surface reaching for one sees the other. Driven against a 500 on `/api/projects`:
//
//   before:  "No projects yet"   + the New-project CTA, no alert, no retry
//   after:   alert → heading "Couldn't load your projects"
//                  → paragraph "probe-induced failure"      ← the server's own message
//                  → button "Retry"
//
// 🔑 WHY `role="alert"` HERE AND NOT ON `EmptyState`. A load failure is unrequested bad news that
// changes what the screen MEANS, so it interrupts. "You have none" is a normal answer to a normal
// question, so `EmptyState` has no live region: the same distinction the toast host draws between
// assertive errors and polite confirmations.
//
// 🪤 ORDER MATTERS. `data === undefined` is true for the loading, error AND empty branches, so the
// error test comes first or it is unreachable:
//
//     {data === undefined && error ? <LoadError … />
//      : data === undefined      ? <ListSkeleton />
//      : data.length === 0       ? <EmptyState … />
//      : rows}
//
// Which surfaces adopt it is `ui/loadErrorCensus.test.ts`'s, and how each one reaches it is
// `pages/loadErrorSurfaces.test.tsx`'s.

describe('LoadError announces and offers recovery', () => {
  it('lives in the list kit, beside EmptyState', () => {
    // Co-located on purpose: the two are alternative answers to the same condition, and a surface
    // reaching for one should see the other. This file's one import of both is the pin: moving
    // either out of `ListScaffold` breaks it.
    expect([LoadError, EmptyState].map((c) => typeof c)).toEqual(['function', 'function'])
  })

  it('is an alert: a load failure interrupts', () => {
    const { container } = render(<LoadError what="projects" />)
    expect(container.querySelector('[role="alert"]'), 'a failed load must be announced').not.toBeNull()
  })

  it('EmptyState is NOT an alert: "you have none" is a normal answer', () => {
    // The contrast is the point: if both were alerts, every empty list would interrupt.
    const { container } = render(<EmptyState title="No projects yet" />)
    expect(container.querySelector('[role="alert"]')).toBeNull()
  })

  it('offers a retry that re-runs the fetch', () => {
    const onRetry = vi.fn()
    render(<LoadError what="projects" error={new Error('x')} onRetry={onRetry} />)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('omits the retry button when the surface cannot retry', () => {
    render(<LoadError what="projects" error={new Error('x')} />)
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull()
  })

  it('hides the decorative icon from assistive tech', () => {
    // The heading already carries the meaning; an announced glyph is noise.
    const { container } = render(<LoadError what="projects" />)
    expect(container.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true')
  })
})

// ── What the alert says ─────────────────────────────────────────────────────────────────────────
//
// The headline names what failed, and the body explains it: the server's own message when it
// wrote one, and otherwise a sentence of ours. Two things used to go wrong in the body.
//
//   · It read "Your ${what} are safe", ungrammatical for the many SINGULAR nouns callers pass
//     ("Your project are safe"). The reassurance is noun-free now; the headline still names the
//     thing.
//   · It rendered `(error as Error)?.message || <our sentence>`, and `?.message` is truthy for a
//     browser fetch rejection, so our sentence was unreachable in the commonest failure of all.
//     What the user read instead, under a headline that had already said what failed:
//
//         Chrome   "Failed to fetch"
//         Safari   "Load failed"
//         Firefox  "NetworkError when attempting to fetch resource."
//         any 5xx  "HTTP 502"   ← errText's OWN placeholder for a body it refused to show
//
//     `readableErrText` drops exactly those, and the body falls back to our sentence.

const FALLBACK = /this is just a load error, and nothing was lost/

const SAYS: Array<{ row: string; what: string; error?: unknown; says: RegExp[]; saysNot?: RegExp[] }> = [
  { row: 'the headline names what failed', what: 'projects', says: [/Couldn't load your projects/] },
  // A bare "something went wrong" hides the one detail that helps.
  { row: "the server's own message", what: 'projects', error: new Error('gateway timed out'), says: [/gateway timed out/] },
  { row: 'an error with no message: our sentence', what: 'projects', error: {}, says: [FALLBACK] },
  { row: 'no error at all: our sentence', what: 'loops', says: [FALLBACK] },
  {
    // The regression that motivated the rewrite: a singular noun must not produce "are safe".
    row: 'a SINGULAR noun reads grammatically',
    what: 'project', error: {}, says: [/Couldn't load your project/], saysNot: [/are safe/],
  },
  {
    // Only the headline interpolates the noun: a body that did would need a noun of one number.
    row: 'the body names no noun',
    what: 'zebras', error: {}, says: [/^Couldn't load your zebras(?!.*zebras)/],
  },
  {
    row: 'the body names no noun when the server answered, either',
    what: 'zebras', error: new Error('HTTP 500'),
    says: [/^Couldn't load your zebras(?!.*zebras)/, /The server answered with an error \(HTTP 500\)/],
  },
  {
    row: 'a browser fetch rejection: our sentence, not "Failed to fetch"',
    what: 'projects', error: new Error('Failed to fetch'),
    says: [/Couldn't load your projects/, FALLBACK], saysNot: [/Failed to fetch/],
  },
  { row: 'a bare HTTP status: our sentence', what: 'tasks', error: new Error('HTTP 502'), says: [FALLBACK], saysNot: [/HTTP 502/] },
  {
    // 🪤 Without this row, suppressing every message would satisfy the two above.
    row: 'a backend message reaches the user, in place of our sentence',
    what: 'agents', error: new Error('Gateway is running an older API version'),
    says: [/Gateway is running an older API version/], saysNot: [FALLBACK],
  },
]

describe('LoadError says what failed in a sentence', () => {
  it.each(SAYS.map((r) => [r.row, r] as const))('%s', (_, r) => {
    render(<LoadError what={r.what} error={r.error} />)
    const text = screen.getByRole('alert').textContent ?? ''
    for (const re of r.says) expect(text).toMatch(re)
    for (const re of r.saysNot ?? []) expect(text).not.toMatch(re)
  })
})

// ── readableErrText suppresses only what a user cannot act on ───────────────────────────────────
//
// 🪤 THE RISK THIS CARRIES IS THE OPPOSITE ONE, so it is tested harder than the bug: a broad
// "looks technical" heuristic would swallow backend-authored messages, which are the ones most
// worth reading. The predicate is a closed set, and the kept cases outnumber the dropped ones on
// purpose.

const DROPPED = [
  'Failed to fetch',
  'failed to fetch',
  'Load failed',
  'NetworkError when attempting to fetch resource.',
  'network error',
  'TypeError: Failed to fetch',
  'The Internet connection appears to be offline.',
  'HTTP 502',
  'HTTP 500',
  'HTTP 401',
]

// Each is something a person can act on or search for; a regression that ate them would be a worse
// bug than the one this closes.
const KEPT = [
  'name is required',
  'evals_disabled',
  'A workflow with that name already exists',
  'Gateway is running an older API version',
  'Permission denied: the token lacks tools:write',
  'HTTP 502 Bad Gateway — upstream refused the tunnel', // NOT a bare status: carries detail
  'Failed to fetch the manifest from the store',        // NOT the browser string: says what
]

const SHAPES: Array<[string, unknown, string]> = [
  ['undefined', undefined, ''],
  ['null', null, ''],
  ['an empty string', '', ''],
  ['a blank string', '   ', ''],
  ['a bare object, whose message is not trusted', { message: 'not an Error instance' }, ''],
  ['a thrown string, which is still a message', 'a plain string rejection', 'a plain string rejection'],
]

describe('readableErrText', () => {
  it.each(DROPPED)('drops %j, so the surface falls back to its own sentence', (text) => {
    expect(readableErrText(new Error(text))).toBe('')
  })

  it.each(KEPT)('keeps %j', (text) => {
    expect(readableErrText(new Error(text)), 'a message with content must reach the user').toBe(text)
  })

  it.each(SHAPES)('reads %s', (_, value, out) => {
    expect(readableErrText(value)).toBe(out)
  })
})
