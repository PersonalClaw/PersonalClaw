import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import type { ComposerProps } from './types'

// ── A draft typed during a turn keeps the turn's Stop in reach (#5867) ─────────────────────────
//
// While a turn runs with nothing typed, the composer's send button is "Stop this turn". Once the
// owner types a draft, that button steers the draft into the turn or queues it behind, so a second,
// secondary "Stop this turn" sits beside it: the same action, the same name and tooltip, and a
// 44 px target at compact widths, because a phone has no Esc. With nothing typed there is still
// one Stop, the send button, and an idle composer has none.

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: new Proxy(real.api, {
      get: (target, key) => (key === 'dashboardConfig'
        ? () => Promise.resolve({ send_on_enter: true })
        : (target as Record<string | symbol, unknown>)[key]),
    }),
  }
})

const { Composer } = await import('../Composer')

const CONTROLS = { agent: false, model: false, approval: false, reasoning: false, attach: false, mic: false, optimize: false }
const STEER = 'Steer — send into the running turn'
const QUEUE = 'Queue — sent when this turn ends'

function composer(props: Partial<ComposerProps> = {}) {
  return <Composer value="" onChange={() => {}} onSend={() => {}} onStop={() => {}} controls={CONTROLS} {...props} />
}

const stops = () => screen.queryAllByRole('button', { name: 'Stop this turn' })

afterEach(() => cleanup())

describe('a draft typed during a turn keeps the turn\'s Stop beside Steer or Queue', () => {
  it.each<[string, Partial<ComposerProps>, string]>([
    ['a turn that takes the draft in', { canSteer: true }, STEER],
    ['a turn the draft queues behind', {}, QUEUE],
  ])('%s: a secondary Stop this turn sits beside the send button, and stops the turn', (_name, props, sendLabel) => {
    const onStop = vi.fn()
    const onSend = vi.fn()
    render(composer({ streaming: true, value: 'and check the tests', canQueue: true, onStop, onSend, ...props }))
    expect(screen.getByRole('button', { name: sendLabel }), 'the send button steers or queues the draft').toBeInTheDocument()
    expect(stops(), 'one Stop this turn beside it').toHaveLength(1)
    const stop = stops()[0]
    expect(stop.getAttribute('title'), 'its tooltip shows the key that stops the turn too').toBe('Stop this turn (Esc)')
    expect(stop.className.split(/\s+/), 'it is secondary: the send button keeps the fill').not.toContain('bg-primary')
    expect(stop.className.split(/\s+/), 'a 44 px target at compact widths').toEqual(expect.arrayContaining(['max-md:min-h-11', 'max-md:min-w-11']))
    fireEvent.click(stop)
    expect(onStop).toHaveBeenCalledTimes(1)
    expect(onSend, 'stopping sends nothing').not.toHaveBeenCalled()
  })

  it('with nothing typed, the send button is the one Stop this turn', () => {
    const onStop = vi.fn()
    render(composer({ streaming: true, canQueue: true, canSteer: true, onStop }))
    expect(stops()).toHaveLength(1)
    expect(screen.queryByRole('button', { name: STEER })).toBeNull()
    expect(screen.queryByRole('button', { name: QUEUE })).toBeNull()
    fireEvent.click(stops()[0])
    expect(onStop).toHaveBeenCalledTimes(1)
  })

  it('with no turn running, a draft shows Send and no Stop', () => {
    render(composer({ value: 'and check the tests', canQueue: true }))
    expect(screen.getByRole('button', { name: 'Send message' })).toBeInTheDocument()
    expect(stops()).toHaveLength(0)
  })
})
