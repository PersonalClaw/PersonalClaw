import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ProviderCard } from './ProviderCard'
import { api, type SettingsProvider } from '../../lib/api'

// ── A provider card reads what the gateway MEASURED, and says when it has not measured yet ───────
//
// `GET /api/providers` used to run every app's availability hook on the event loop, per request —
// 171.8 s for one of them cold — and the card could only ever be available or not. The hooks now
// run in the gateway's availability child, so the list answers at once and a card can be in a
// state it never had: not measured yet (`checking`), or measured and the check itself failed
// (`unknown`, which is not the app's "no"). And the facts a hook reads change exactly when the user
// installs a package or signs a CLI in, so the card offers "Check again" beside the answer.

function ext(availability: SettingsProvider['availability']): SettingsProvider {
  return {
    name: 'sentence-transformers', displayName: 'Sentence Transformers', enabled: true, managed: true,
    provider: { type: 'model', capabilities: ['embedding'] }, availability,
  }
}

afterEach(() => vi.restoreAllMocks())

function mount(e: SettingsProvider, onChanged = () => {}) {
  return render(<ProviderCard ext={e} open={false} onOpenChange={() => {}} onChanged={onChanged} />)
}

describe('availability on the provider card', () => {
  it('a card the gateway has not measured yet says it is checking — not "unavailable", not silent', () => {
    mount(ext({ state: 'checking', reason: '', checkedAt: null }))
    expect(screen.getByText('checking')).toBeTruthy()
    expect(screen.queryByText('unavailable')).toBeNull()
    // Still usable while it is measured: the toggle is not withheld on a guess.
    expect(screen.getByRole('switch', { name: /toggle sentence-transformers/i })).toBeTruthy()
  })

  it("an app's measured \"no\" shows its reason and offers Check again", async () => {
    const recheck = vi.spyOn(api, 'recheckProviderAvailability')
      .mockResolvedValue({ name: 'sentence-transformers', availability: { state: 'checking', reason: '', checkedAt: null } })
    const onChanged = vi.fn()
    mount(ext({ state: 'unavailable', reason: 'The sentence-transformers package is not installed.', checkedAt: 1 }), onChanged)

    expect(screen.getByText('unavailable')).toBeTruthy()
    expect(screen.getByText('The sentence-transformers package is not installed.')).toBeTruthy()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Check again: Sentence Transformers' })) })
    expect(recheck).toHaveBeenCalledWith('sentence-transformers')
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('a check that could not answer says so, in its own words, and is not dimmed as unavailable', () => {
    const { container } = mount(ext({ state: 'unknown', reason: 'The availability check did not finish within 180 s.', checkedAt: 1 }))
    expect(screen.getByText("couldn't check")).toBeTruthy()
    expect(screen.getByText('The availability check did not finish within 180 s.')).toBeTruthy()
    expect(screen.queryByText('unavailable')).toBeNull()
    expect((container.firstElementChild as HTMLElement).style.opacity).toBe('1')
    expect(screen.getByRole('button', { name: 'Check again: Sentence Transformers' })).toBeTruthy()
  })

  it('an available card carries none of it', () => {
    // The floor: a card that always showed a state would pass every test above.
    mount(ext({ state: 'available', reason: '', checkedAt: 1 }))
    for (const text of ['checking', 'unavailable', "couldn't check"]) expect(screen.queryByText(text)).toBeNull()
    expect(screen.queryByRole('button', { name: /check again/i })).toBeNull()
  })
})
