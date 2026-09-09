import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── The header "New trigger" button must not smuggle its click event into the URL ─────────
//
// `onCreate` is `(presetId?: string) => void` and `HeaderControl.onClick` is `() => void`.
// A function taking FEWER-or-optional parameters is assignable to `() => void`, so passing
// the handler by reference type-checks — and React then calls it with the click event, which
// landed in `presetId` and was encoded into the query string. Driven on the real page, the
// primary create button produced:
//
//   #/triggers/new?kind=schedule&preset=%5Bobject%20Object%5D
//
// The preset lookup is deliberately tolerant (an unknown id resolves to null and every field
// keeps its default), so nothing was corrupted. What broke is the thing this surface's own
// contract promises: a create URL that is deep-linkable and survives a reload. A shared link
// reading `[object Object]` is indistinguishable from a broken one.
//
// tsc cannot catch this class, so the assertion has to be behavioural.

const { EMPTY } = vi.hoisted(() => ({ EMPTY: [] as unknown[] }))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    schedules: () => Promise.resolve({ jobs: EMPTY }),
    hooks: () => Promise.resolve(EMPTY),
    storeTriggers: () => Promise.resolve(EMPTY),
    eventTriggers: () => Promise.resolve(EMPTY),
    actionProviders: () => Promise.resolve(EMPTY),
    autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
    triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }),
  },
}))

const { TriggersSection } = await import('./TriggersSection')

const mount = () => {
  const navigate = vi.fn()
  const view = render(
    <TriggersSection sub="" navigate={navigate} navEpoch={0} query={{}} setQuery={() => {}} />,
  )
  return { ...view, navigate }
}

describe('the header create button', () => {
  beforeEach(() => vi.clearAllMocks())

  it('navigates to the blank create flow with NO preset in the URL', async () => {
    const { navigate } = mount()
    const button = await waitFor(() => screen.getByRole('button', { name: /new trigger/i }))
    await userEvent.click(button)

    expect(navigate).toHaveBeenCalledTimes(1)
    const target = String(navigate.mock.calls[0][0])
    expect(target, 'the blank path must not carry a preset at all').not.toMatch(/preset=/)
    expect(target, 'and certainly not a stringified event object').not.toMatch(/object%20Object|object Object/)
    expect(target).toBe('triggers/new')
  })
})
