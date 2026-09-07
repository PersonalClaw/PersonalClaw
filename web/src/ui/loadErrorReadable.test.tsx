import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readableErrText } from '../lib/errText'
import { LoadError } from './ListScaffold'

// ── A failure explanation the reader cannot use is worse than the sentence it replaced ───────────
//
// `LoadError` renders at 122 sites (105 of them passing an `error`), and its body was
//
//     {(error as Error)?.message || "The server didn't respond — …"}
//
// `?.message` is truthy for a browser fetch rejection, so that written sentence was UNREACHABLE in
// the commonest failure of all. What the user read instead, directly under a headline that had
// already said "Couldn't load your projects":
//
//     Chrome   "Failed to fetch"
//     Safari   "Load failed"
//     Firefox  "NetworkError when attempting to fetch resource."
//     any 5xx  "HTTP 502"   ← errText's OWN placeholder for a body it refused to show
//
// `errEnvelope` could never have caught these: it takes a `Response`, and a rejected `fetch` has
// none. `HTTP <status>` is the mirror case — correct in a one-line FieldError under an input, wrong
// as prose beneath a headline that already named the failure.
//
// 🪤 THE RISK THIS FIX CARRIES IS THE OPPOSITE ONE, so it is tested harder than the bug itself: a
// broad "looks technical" heuristic would swallow backend-authored messages, which are the ones
// most worth reading. The predicate is a closed set, and the "must survive" cases below outnumber
// the suppressed ones deliberately.

describe('readableErrText suppresses only what a user cannot act on', () => {
  for (const opaque of [
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
  ]) {
    it(`suppresses ${JSON.stringify(opaque)}`, () => {
      expect(readableErrText(new Error(opaque)), 'the surface should fall back to its own sentence').toBe('')
    })
  }

  // The half that matters more. Every one of these is something a person can act on or search for,
  // and a regression that ate them would be a worse bug than the one this file closes.
  for (const keep of [
    'name is required',
    'evals_disabled',
    'A workflow with that name already exists',
    'Gateway is running an older API version',
    'Permission denied: the token lacks tools:write',
    'HTTP 502 Bad Gateway — upstream refused the tunnel', // NOT a bare status: carries detail
    'Failed to fetch the manifest from the store',        // NOT the browser string: says what
  ]) {
    it(`keeps ${JSON.stringify(keep)}`, () => {
      expect(readableErrText(new Error(keep)), 'a message with content must reach the user').toBe(keep)
    })
  }

  it('handles the shapes that are not Errors at all', () => {
    expect(readableErrText(undefined)).toBe('')
    expect(readableErrText(null)).toBe('')
    expect(readableErrText('')).toBe('')
    expect(readableErrText('   ')).toBe('')
    expect(readableErrText({ message: 'not an Error instance' }), 'a bare object carries no message we trust').toBe('')
    expect(readableErrText('a plain string rejection'), 'but a thrown string is still a message').toBe('a plain string rejection')
  })
})

describe('LoadError shows a sentence rather than console text', () => {
  it('a browser fetch rejection renders the written fallback, not "Failed to fetch"', () => {
    render(<LoadError what="projects" error={new Error('Failed to fetch')} />)
    const alert = screen.getByRole('alert')
    expect(alert.textContent, 'the headline still names what failed').toMatch(/Couldn't load your projects/)
    expect(alert.textContent, 'and the explanation is the written one').toMatch(/this is just a load error/)
    expect(alert.textContent, 'console text must not reach the user').not.toMatch(/Failed to fetch/)
  })

  it('a bare HTTP status renders the written fallback too', () => {
    render(<LoadError what="tasks" error={new Error('HTTP 502')} />)
    expect(screen.getByRole('alert').textContent).not.toMatch(/HTTP 502/)
  })

  it('a backend message still reaches the user — the fix must not swallow it', () => {
    // 🪤 Without this, suppressing everything would satisfy the two tests above.
    render(<LoadError what="agents" error={new Error('Gateway is running an older API version')} />)
    const alert = screen.getByRole('alert')
    expect(alert.textContent).toMatch(/Gateway is running an older API version/)
    expect(alert.textContent, 'and it replaces the generic sentence rather than joining it')
      .not.toMatch(/this is just a load error/)
  })

  it('no error at all still renders the fallback', () => {
    render(<LoadError what="loops" />)
    expect(screen.getByRole('alert').textContent).toMatch(/this is just a load error/)
  })
})
