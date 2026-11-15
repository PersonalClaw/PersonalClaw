import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { NudgeLoop } from '../../../lib/api'

// ── Auto-nudge stops after ten cycles unless you choose no limit ───────────────────────────────
//
// Max cycles used to start at 0, which means no limit, and the modal said nothing about it: a nudge
// armed as the modal opened ran an agent turn about every minute until someone pressed Stop. The
// field now starts at the gateway's own bound, 10, a hint under it says when the nudge stops (or
// that 0 keeps it going), and the loop's line shows the limit beside the cycles fired and says
// Stopped once the nudge has stopped. What is read is what a person sees and what Arm sends.

const h = vi.hoisted(() => ({
  loop: null as NudgeLoop | null,
  calls: [] as Array<[string, unknown[]]>,
}))

vi.mock('../../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../../lib/api')>()
  const record = (name: string, answer: unknown) => (...args: unknown[]) => {
    h.calls.push([name, args])
    return Promise.resolve(answer)
  }
  const api = {
    ...real.api,
    autonudgeGet: (...args: unknown[]) => record('autonudgeGet', { enabled: true, loop: h.loop })(...args),
    autonudgeStart: (...args: unknown[]) => record('autonudgeStart', { ok: true, loop: h.loop })(...args),
    autonudgeUpdate: (...args: unknown[]) => record('autonudgeUpdate', { ok: true, loop: h.loop })(...args),
    autonudgeDelete: (...args: unknown[]) => record('autonudgeDelete', { ok: true })(...args),
  }
  return { ...real, api }
})

import { AutoNudgeMenuItem } from './AutoNudgeMenuItem'

const armed = (over: Partial<NudgeLoop> = {}): NudgeLoop => ({
  id: 'nudge:0a1b2c3d', session_name: 'chat-1-123', message: 'Keep going.', idle_secs: 60,
  max_cycles: 10, cycle_count: 0, active: true, last_fire_ts: 0, created_ts: 1_800_000_000,
  ...over,
})

beforeEach(() => {
  h.loop = null
  h.calls.length = 0
})

async function openModal() {
  render(<AutoNudgeMenuItem session="chat-1-123" onOpen={() => {}} />)
  fireEvent.click(screen.getByRole('button', { name: /Auto-nudge/ }))
  return (await screen.findByLabelText('Max cycles')) as HTMLInputElement
}

/** The body each autonudgeStart call sent. */
const starts = () => h.calls.filter(([n]) => n === 'autonudgeStart').map(([, a]) => a[0] as Record<string, unknown>)

describe('Auto-nudge stops after ten cycles unless you choose no limit', () => {
  it('starts Max cycles at 10, says when it stops, and Arm sends 10', async () => {
    const field = await openModal()
    expect(field.value).toBe('10')
    expect(screen.getByText('It stops by itself after 10 cycles. 0 means no limit.')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Auto-nudge message'), { target: { value: 'Keep going.' } })
    fireEvent.click(screen.getByRole('button', { name: /Arm/ }))
    await waitFor(() => expect(starts()).toHaveLength(1))
    expect(starts()[0].max_cycles).toBe(10)
  })

  it('says 0 is no limit when you set it, and Arm sends 0', async () => {
    const field = await openModal()
    fireEvent.change(field, { target: { value: '0' } })
    fireEvent.blur(field)
    expect(await screen.findByText('No limit: it keeps nudging this chat until you press Stop.')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Auto-nudge message'), { target: { value: 'Keep going.' } })
    fireEvent.click(screen.getByRole('button', { name: /Arm/ }))
    await waitFor(() => expect(starts()).toHaveLength(1))
    expect(starts()[0].max_cycles).toBe(0)
  })

  it.each([
    [{ cycle_count: 3, max_cycles: 10 }, 'Active · 3 / 10 cycles fired.'],
    [{ cycle_count: 3, max_cycles: 0 }, 'Active · 3 cycles fired · no limit.'],
    [{ cycle_count: 1, max_cycles: 0 }, 'Active · 1 cycle fired · no limit.'],
    [{ active: false, cycle_count: 10, max_cycles: 10 }, 'Stopped · 10 / 10 cycles fired.'],
  ])('an armed loop %o reads %s', async (over, line) => {
    h.loop = armed(over)
    await openModal()
    expect(await screen.findByText(line)).toBeTruthy()
  })

  it('keeps a nudge armed with no limit at 0, and Update sends 0', async () => {
    h.loop = armed({ max_cycles: 0, cycle_count: 2 })
    const field = await openModal()
    await waitFor(() => expect(field.value).toBe('0'))
    fireEvent.click(screen.getByRole('button', { name: /Update/ }))
    await waitFor(() => expect(h.calls.filter(([n]) => n === 'autonudgeUpdate')).toHaveLength(1))
    const [, args] = h.calls.find(([n]) => n === 'autonudgeUpdate')!
    expect(args[1]).toMatchObject({ max_cycles: 0 })
  })
})
