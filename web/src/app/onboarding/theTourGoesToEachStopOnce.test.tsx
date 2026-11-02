import { afterEach, expect, it } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { PRODUCT_TOUR_STOPS, ProductTour } from './ProductTour'
import { consumeProductTourRequest, requestProductTour } from './tourLaunch'

// ── The tour takes you to each stop's surface once ───────────────────────────────────────────────
//
// The shell's `navigate` (`useHashRoute`) is a new function on every render, and every call
// re-renders the shell, even to the address it is already at. The approvals stop's route carries a
// query (`dashboard?view=attention`, since Home opens on Overview), which the shell's route id
// (`dashboard`) never equals. A tour that navigated whenever `navigate` changed navigated again on
// every render, without end: the browser run's tour test spun until its job timed out.

/** Past this many navigations the harness stops re-rendering, so an endless loop ends as a red. */
const CAP = 25

/** The shell as the tour sees it: its route id, and a `navigate` like `useHashRoute`'s. */
function Shell({ calls }: { calls: string[] }) {
  const [route, setRoute] = useState('dashboard')
  const [, setEpoch] = useState(0)
  const navigate = (path: string) => {
    calls.push(path)
    if (calls.length > CAP) return
    setRoute(path.split('?')[0])
    setEpoch((n) => n + 1)
  }
  return <ProductTour route={route} navigate={navigate} />
}

afterEach(() => {
  cleanup()
  consumeProductTourRequest()
})

it("goes to each stop's surface once, the approvals stop's query and all", async () => {
  const calls: string[] = []
  const user = userEvent.setup()
  render(<Shell calls={calls} />)
  act(() => { requestProductTour() })

  for (let n = 0; n < PRODUCT_TOUR_STOPS.length - 1; n += 1) {
    await user.click(await screen.findByRole('button', { name: /Next/ }))
  }
  expect(calls).toEqual(['chat', 'inbox', 'dashboard?view=attention', 'settings'])
})
