/**
 * A preference the chat learns is said where it was learned, is still said after a reload, and
 * can be forgotten from right there.
 *
 * 🔴 Before: the only notice was a live chip on the socket. A reload — or a gateway restart in
 * the middle of the turn — never replayed it, so a preference could be saved and injected into
 * every later prompt with nothing on the page saying so; and the chip's link opened the Studio,
 * which shows the preference as raw JSON with no way to forget it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { hydrateTurns, type ActivitySegment, type HistMsg } from './chatTypes'

const KEY = 'pref.facet.style.0123456789'
const h = vi.hoisted(() => ({ forget: vi.fn(), confirm: vi.fn() }))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return { ...real, api: { ...real.api, memoryFacetForget: h.forget } }
})
vi.mock('../../ui/dialog', async (orig) => {
  const real = await orig<typeof import('../../ui/dialog')>()
  return { ...real, confirmDestructive: h.confirm }
})

const { ContextLedger } = await import('./ContextLedger')

beforeEach(() => {
  h.forget.mockReset().mockResolvedValue({ ok: true })
  h.confirm.mockReset().mockResolvedValue(true)
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView
})
afterEach(cleanup)

describe('what a turn learned survives a reload', () => {
  it('rebuilds the learned chip from the turn record, with the key that undoes it', () => {
    const messages: HistMsg[] = [
      { role: 'user', content: 'From now on, keep your answers short.', ts: 't1' },
      {
        role: 'assistant', content: 'Will do.', ts: 't2',
        meta: { learned: [{ origin: 'facet', text: 'keep your answers short', ref: KEY }] },
      },
    ]
    const answer = hydrateTurns(messages).find((t) => t.role === 'assistant')!
    const learned = answer.segments.filter(
      (s): s is ActivitySegment => s.kind === 'activity' && (s as ActivitySegment).activityKind === 'learned')
    expect(learned).toEqual([{
      kind: 'activity', text: 'Learned: keep your answers short', activityKind: 'learned', origin: 'facet', ref: KEY,
    }])
  })

  it('adds nothing to a turn that learned nothing', () => {
    const answer = hydrateTurns([{ role: 'user', content: 'hi' }, { role: 'assistant', content: 'Hello.' }])
      .find((t) => t.role === 'assistant')!
    expect(answer.segments.some((s) => (s as ActivitySegment).activityKind === 'learned')).toBe(false)
  })
})

describe('a learned preference in the turn ledger', () => {
  function open() {
    render(<ContextLedger learned="Learned: keep your answers short" learnedOrigin="facet" learnedRef={KEY} />)
    fireEvent.click(screen.getByRole('button', { name: /learned 1/ }))
  }

  it('links to the list that pins or forgets it, at its row', () => {
    open()
    expect(screen.getByRole('link', { name: /Learned preferences/ }).getAttribute('href'))
      .toBe(`#/settings/memory?tab=settings&pref=${encodeURIComponent(KEY)}`)
  })

  it('forgets it from right there, after the same confirm the list asks', async () => {
    open()
    fireEvent.click(screen.getByRole('button', { name: 'Forget it' }))

    await waitFor(() => expect(h.forget).toHaveBeenCalledWith(KEY))
    expect(h.confirm.mock.calls[0][0]).toBe('Forget this preference?')
    expect(await screen.findByText(/Forgotten — it no longer reaches the model/)).toBeTruthy()
  })

  it('forgets nothing when the confirm is declined', async () => {
    h.confirm.mockResolvedValue(false)
    open()
    fireEvent.click(screen.getByRole('button', { name: 'Forget it' }))
    await waitFor(() => expect(h.confirm).toHaveBeenCalled())
    expect(h.forget).not.toHaveBeenCalled()
  })

  it('offers no Forget for a lesson, which is reviewed where its link goes', () => {
    render(<ContextLedger learned="Learned: never force-push to main" learnedOrigin="lesson" />)
    fireEvent.click(screen.getByRole('button', { name: /learned 1/ }))
    expect(screen.queryByRole('button', { name: 'Forget it' })).toBeNull()
  })
})
