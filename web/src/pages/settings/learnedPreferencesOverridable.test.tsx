/**
 * #1783 clause 3 (frontend) — a learned preference can be pinned and forgotten from Settings.
 *
 * `Facet.pinned` and `Facet.forgotten` persisted, `decayed_stability` and `facet_state` both
 * branched on them, and the identity report rendered the resulting state — while nothing outside
 * the test suite could set either flag. The documented user override ("pin what you want kept,
 * forget what you got wrong") existed only as a field on a dataclass.
 *
 * Driven through the real panel, not a lifted handler: the defect was an affordance that did not
 * exist on a surface, so the claim under test is that a user who opens Settings → Memory finds
 * these controls and that clicking them reaches the two write routes. A handler-level test would
 * pass with the section never rendered, which is the shape that let this ship inert.
 *
 * The forget dialog's WORDING is asserted too, because forgetting is irreversible by design
 * (`decayed_stability` tests `forgotten` before `pinned`, and re-observation cannot resurrect it).
 * A one-way door the user was not told about is a worse defect than the missing button.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import type { MemoryFacet } from '../../lib/api'

const FACET: MemoryFacet = {
  key: 'pref.facet.style.0123456789',
  cls: 'style',
  text: 'keep your answers short',
  cue: 'explicit',
  stability: 0.42,
  stored_stability: 0.9,
  state: 'Provisional',
  updated_at: '2026-08-01T00:00:00Z',
  pinned: false,
  forgotten: false,
}

let facets: ReturnType<typeof vi.fn>
let pinFacet: ReturnType<typeof vi.fn>
let forgetFacet: ReturnType<typeof vi.fn>
let confirmDestructive: ReturnType<typeof vi.fn>

/** The confirm options the user would have been shown for the last dialog raised. */
const lastConfirm = () => confirmDestructive.mock.calls.at(-1) ?? []

async function mountSettings(rows: Partial<MemoryFacet>[] = [{}]) {
  const list = rows.map((r) => ({ ...FACET, ...r }))
  facets = vi.fn(() => Promise.resolve(list))
  pinFacet = vi.fn(() => Promise.resolve({ ok: true, pinned: true }))
  forgetFacet = vi.fn(() => Promise.resolve({ ok: true }))
  confirmDestructive = vi.fn(() => Promise.resolve(true))

  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as object),
        memoryStats: () => Promise.resolve(null),
        memorySettings: () => Promise.resolve({ history_idle_hours: 2, history_max_days: 90 }),
        memoryVaultStatus: () => Promise.resolve(null),
        memoryFacets: facets,
        memoryFacetPin: pinFacet,
        memoryFacetForget: forgetFacet,
      },
    }
  })
  vi.doMock('../../ui/dialog', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return { ...real, confirmDestructive }
  })

  const { MemoryPanel } = await import('./MemoryPanel')
  // The tab is URL state (`useQueryParam`), so it is addressed rather than clicked: with a no-op
  // `setQuery` a click on the tab strip cannot change tabs, and the panel would stay on Studio
  // forever. `?tab=settings` is also how a user really arrives here from a deep link.
  render(<MemoryPanel query={{ tab: 'settings' }} setQuery={() => {}} />)
  expect(screen.getByRole('tab', { name: /settings/i }).getAttribute('aria-selected')).toBe('true')
  await waitFor(() => expect(screen.getByText('Learned preferences')).toBeTruthy())
  return list
}

beforeEach(() => {
  cleanup()
  vi.resetModules()
  sessionStorage.clear()
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView
})
afterEach(() => cleanup())

describe('learned preferences in Settings → Memory', () => {
  it('lists the facet with its decayed strength and its state', async () => {
    // 🔴 The whole clause in one assertion: this section did not exist, and the Studio's raw
    // `pref.facet.*` JSON shows neither the decayed score nor the two flags.
    await mountSettings()
    await waitFor(() => expect(screen.getByText('keep your answers short')).toBeTruthy())
    expect(screen.getByText(/style · Provisional/)).toBeTruthy()
    expect(screen.getByText('0.42')).toBeTruthy()
    // The stored score rides along so the decay is visible rather than inferred.
    expect(screen.getByText(/decayed from 0\.90/)).toBeTruthy()
  })

  it('pins the facet the user picked, by key', async () => {
    await mountSettings()
    fireEvent.click(await screen.findByRole('button', { name: /^pin$/i }))
    await waitFor(() => expect(pinFacet).toHaveBeenCalledWith('pref.facet.style.0123456789', true))
  })

  it('offers Unpin for an already-pinned facet and releases it', async () => {
    // One route, not a pin/unpin pair — so the control has to carry the direction.
    await mountSettings([{ pinned: true, state: 'Active', stability: 1 }])
    fireEvent.click(await screen.findByRole('button', { name: /^unpin$/i }))
    await waitFor(() => expect(pinFacet).toHaveBeenCalledWith('pref.facet.style.0123456789', false))
  })

  it('confirms before forgetting, and states that it cannot be undone', async () => {
    // 🔑 The irreversibility, in the dialog. Two things the user cannot discover by trying:
    // pinning does not bring it back, and the assistant observing the preference again does not
    // either. Both are asserted because both are surprising.
    await mountSettings()
    fireEvent.click(await screen.findByRole('button', { name: /forget "keep your answers short"/i }))
    await waitFor(() => expect(confirmDestructive).toHaveBeenCalled())
    const [title, body] = lastConfirm()
    expect(String(title)).toMatch(/forget this preference/i)
    // The body is JSX, so its prose is read out of the serialized element tree with the source's
    // own line breaks folded back into spaces — otherwise a sentence that wraps in the editor
    // would be unmatchable, and the assertions would silently have to shrink to single words.
    const text = JSON.stringify(body).replace(/\\n\s*/g, ' ')
    expect(text).toMatch(/drops out of the profile block/i)
    expect(text).toMatch(/cannot be undone/i)
    expect(text).toMatch(/not by pinning it/i)
    expect(text).toMatch(/observing the same preference again/i)
    expect(text, 'the row survives, marked forgotten — say so').toMatch(/never re-learned/i)
    await waitFor(() => expect(forgetFacet).toHaveBeenCalledWith('pref.facet.style.0123456789'))
  })

  it('forgets nothing when the dialog is dismissed', async () => {
    // Vacuity control for the rail above: a confirm that fired is only meaningful if declining
    // it stops the irreversible write.
    await mountSettings()
    confirmDestructive.mockResolvedValueOnce(false)
    fireEvent.click(await screen.findByRole('button', { name: /forget "keep your answers short"/i }))
    await waitFor(() => expect(confirmDestructive).toHaveBeenCalled())
    expect(forgetFacet).not.toHaveBeenCalled()
  })

  it('keeps a forgotten facet visible, struck through and final', async () => {
    // Forgetting is final, so this list is the ONLY place a retirement stays visible — hiding it
    // would leave the user unable to tell a preference they retired from one never learned.
    await mountSettings([{ forgotten: true, state: 'Dropped', stability: 0 }])
    expect(await screen.findByText('1 forgotten')).toBeTruthy()
    // Not in the live list: no Pin/Forget control is offered for a row that cannot come back.
    expect(screen.queryByRole('button', { name: /^pin$/i })).toBeNull()
    expect(screen.getByText(/Nothing inferred yet/i)).toBeTruthy()
  })

  it('says what to do when nothing has been learned yet', async () => {
    await mountSettings([])
    expect(await screen.findByText(/Nothing inferred yet/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^pin$/i })).toBeNull()
  })
})
