import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { phaseOf } from '../../lib/runningRows'
import { mountPage, navigate, PID, running } from '../../test/projectPageHarness'
import { RunningLane } from '../dashboard/RunningLane'

// ── A running card on a project's page opens its work (§5.5) ───────────────────────────────────
//
// Running now on a project's page is Home's `RunningLane` with `opens="work"`: a card's row-wide
// target is named as a work row is, "<kind>: <title>, <phase>", and opens the work at its route,
// and the card draws no second Open link. Home keeps the default: a card opens its detail, and its
// Open link goes to the work.

beforeEach(() => { resetDataStore(); localStorage.clear(); navigate.mockReset() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDataStore() })

const SENSORS = running('loop:e2e0fenc', 'Fix the irrigation sensor tests', PID, { line: '18 of 24 passed', route: '#/code/e2e0fenc' })
const phase = phaseOf(SENSORS).label.toLowerCase()

describe("a project's Running now", () => {
  it("names a card's target as a work row and opens the work at its route, with no Open link", async () => {
    mountPage({ running: [SENSORS] })
    const target = await screen.findByRole('button', { name: `Code session: Fix the irrigation sensor tests, ${phase}` })
    const card = target.closest('li')!
    expect(within(card).queryByRole('link', { name: /^Open/ }), 'the whole card is the way to the work').toBeNull()
    expect(within(card).queryByRole('button', { name: /^Show the details of/ })).toBeNull()
    await userEvent.click(target)
    expect(navigate).toHaveBeenCalledWith('#/code/e2e0fenc')
  })

  it('a card with no route to open has no target', async () => {
    mountPage({ running: [{ ...SENSORS, route: '' }] })
    await screen.findByText('Fix the irrigation sensor tests')
    expect(screen.queryByRole('button', { name: /^Code session: Fix the irrigation sensor tests/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Show the details of/ }), 'nor a detail to open in its place').toBeNull()
  })
})

describe("Home's Running now keeps its detail (the control)", () => {
  it('opens the detail by default, and its Open link goes to the work', async () => {
    const onOpen = vi.fn()
    render(<RunningLane rows={[SENSORS]} loading={false} failed={false} empty="" onOpen={onOpen} />)
    await userEvent.click(screen.getByRole('button', { name: 'Show the details of Fix the irrigation sensor tests' }))
    expect(onOpen).toHaveBeenCalledWith(SENSORS)
    expect(screen.getByRole('link', { name: /^Open Fix the irrigation sensor tests/ }).getAttribute('href')).toBe('#/code/e2e0fenc')
  })
})
