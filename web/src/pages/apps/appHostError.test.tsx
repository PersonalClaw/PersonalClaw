import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { AppHostPage } from './AppHostPage'
import { api, ApiError } from '../../lib/api'

// A8: the app host used to render ONE sentence — `App "x" is not available` — for every
// rejection of GET /api/apps/{name}, conflating two different facts:
//   · 404 = the app is NOT INSTALLED. The route has no other 404 path, so this is an expected,
//     actionable state: the fix is installing it, and the state deep-links to the Store.
//   · anything else = a genuine LOAD FAILURE (gateway hiccup, 500) — retryable, and the honest
//     render is the shared LoadError, not a sentence that blames the app.
// Distinct app names per test: useQuery caches by `app-host:<name>` across renders.

describe('AppHostPage error split', () => {
  it('404 → "isn\'t installed" EmptyState with a Store action, no alert', async () => {
    vi.spyOn(api, 'app').mockRejectedValueOnce(new ApiError("app 'ghost' not installed", 404))
    render(<AppHostPage sub="ghost" navigate={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/isn’t installed/)).toBeTruthy())
    expect(screen.getByRole('button', { name: /open the store/i })).toBeTruthy()
    // Not-installed is a normal answer, not unrequested bad news — no role="alert".
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('the Store action routes to the store view of the Apps page', async () => {
    vi.spyOn(api, 'app').mockRejectedValueOnce(new ApiError("app 'phantom' not installed", 404))
    // Through the router's navigate, not a raw location.hash write — the URL-navigation
    // doctrine (tests/test_url_navigation_doctrine.py) forbids pages mutating the URL directly.
    const navigate = vi.fn()
    render(<AppHostPage sub="phantom" navigate={navigate} />)
    await waitFor(() => expect(screen.getByRole('button', { name: /open the store/i })).toBeTruthy())
    screen.getByRole('button', { name: /open the store/i }).click()
    expect(navigate).toHaveBeenCalledWith('apps?view=store')
  })

  it('a non-404 failure → the retryable LoadError alert, and Retry re-fetches', async () => {
    const spy = vi.spyOn(api, 'app').mockRejectedValueOnce(new ApiError('boom', 500))
    render(<AppHostPage sub="flaky" navigate={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    expect(screen.getByText(/couldn't load your app/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /open the store/i })).toBeNull()
    const calls = spy.mock.calls.length
    screen.getByRole('button', { name: /retry/i }).click()
    await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThan(calls))
  })
})
