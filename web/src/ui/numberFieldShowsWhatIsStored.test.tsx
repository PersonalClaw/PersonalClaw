import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { NumberField } from './forms'

// ── A number that was not stored does not stay in the box ─────────────────────────────────────────
//
// `NumberField` keeps its own draft and re-syncs it only when `value` (what is stored) moves. A save
// that was refused — or a consent the owner declined — moves nothing, so the box kept the number
// that was typed while the setting in effect was another one. Measured on Settings → Guardrails
// (10033.5 shown, $33.50 in effect), and the same shape on External Access's rate caps and a run's
// Max cycles. A caller now returns how the save went, and the field shows `value` again on `false`.

/** A field over a value its owner stores only when `save` says so — painting nothing before then. */
function Owner({ save, initial = 33.5 }: { save: (n: number) => unknown; initial?: number }) {
  const [stored, setStored] = useState(initial)
  return (
    <NumberField value={stored} ariaLabel="Cap" min={0} step={0.01}
      onChange={(n) => {
        const outcome = save(n)
        if (outcome === true) setStored(n)
        if (outcome instanceof Promise) return outcome.then((ok) => { if (ok === true) setStored(n); return ok })
        return outcome
      }} />
  )
}

const box = () => screen.getByRole('spinbutton', { name: 'Cap' }) as HTMLInputElement

async function enter(text: string) {
  await act(async () => {
    fireEvent.change(box(), { target: { value: text } })
    fireEvent.blur(box())
  })
}

describe('NumberField shows what is stored', () => {
  it('a commit refused at once puts the stored value back', async () => {
    render(<Owner save={() => false} />)
    await enter('10033.5')
    expect(box().value).toBe('33.5')
  })

  it('a save that settles false puts it back once it has', async () => {
    let settle: (ok: boolean) => void = () => {}
    render(<Owner save={() => new Promise<boolean>((res) => { settle = res })} />)
    await enter('10033.5')
    // While the question is open the box holds what was typed: that is the number being asked about.
    expect(box().value).toBe('10033.5')
    await act(async () => { settle(false) })
    expect(box().value).toBe('33.5')
  })

  it('a save that rejects puts it back too', async () => {
    render(<Owner save={() => Promise.reject(new Error('refused'))} />)
    await enter('50')
    expect(box().value).toBe('33.5')
  })

  it('a stored value stays', async () => {
    render(<Owner save={() => Promise.resolve(true)} />)
    await enter('100')
    expect(box().value).toBe('100')
  })

  it('a caller that reports nothing keeps its own way of showing a refusal', async () => {
    // The panels that roll their own state back return nothing; their rollback moves `value`.
    const save = vi.fn(() => undefined)
    render(<Owner save={save} />)
    await enter('40')
    expect(save).toHaveBeenCalledWith(40)
    expect(box().value).toBe('40')
  })

  it('a new edit made before a slow refusal lands is not overwritten', async () => {
    let settle: (ok: boolean) => void = () => {}
    render(<Owner save={() => new Promise<boolean>((res) => { settle = res })} />)
    await enter('10033.5')
    await act(async () => { fireEvent.change(box(), { target: { value: '12' } }) })
    await act(async () => { settle(false) })
    expect(box().value).toBe('12')
  })
})
