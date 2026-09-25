import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Segmented } from './Segmented'

// `collapse="wrap"` — the rung for a FORM field in a narrow panel. The task form's six statuses
// measured 649px inside a 420px side panel: Completed was cut off, Cancelled and Skipped sat outside
// the panel, and choosing one scrolled the whole form sideways. 'scroll' would hide the tail behind
// a sideways scroll and 'menu' behind a popover; a value chosen once in a form should show every
// alternative, so the options wrap. jsdom has no layout — the fit itself is measured in the
// browser — so these pin the contract that makes it wrap without changing what it IS.

const OPTIONS = ['Not started', 'In progress', 'Blocked', 'Completed', 'Cancelled', 'Skipped']
  .map((label, i) => ({ key: `k${i}`, label }))

describe('Segmented collapse="wrap"', () => {
  it('wraps inside its container and drops the pill track for a one-row radius', () => {
    render(<Segmented collapse="wrap" ariaLabel="Status" options={OPTIONS} value="k0" onChange={() => {}} />)
    const cls = screen.getByRole('radiogroup', { name: 'Status' }).className.split(/\s+/)
    expect(cls).toEqual(expect.arrayContaining(['inline-flex', 'flex-wrap', 'max-w-full', 'rounded-lgi']))
    expect(cls).not.toContain('rounded-pill')
  })

  it('leaves the default strip exactly as it was', () => {
    render(<Segmented ariaLabel="Status" options={OPTIONS} value="k0" onChange={() => {}} />)
    const cls = screen.getByRole('radiogroup', { name: 'Status' }).className.split(/\s+/)
    expect(cls).toContain('rounded-pill')
    expect(cls).not.toContain('flex-wrap')
  })

  it('is still ONE radiogroup — no measuring probe, no popover listbox', () => {
    render(<Segmented collapse="wrap" ariaLabel="Status" options={OPTIONS} value="k0" onChange={() => {}} />)
    expect(screen.getAllByRole('radiogroup')).toHaveLength(1)
    expect(screen.getAllByRole('radio')).toHaveLength(OPTIONS.length)
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('keeps the roving tab stop and the move-and-select arrow keys', () => {
    const onChange = vi.fn()
    render(<Segmented collapse="wrap" ariaLabel="Status" options={OPTIONS} value="k2" onChange={onChange} />)
    const radios = screen.getAllByRole('radio')
    expect(radios.filter((r) => r.getAttribute('tabindex') === '0')).toHaveLength(1)
    fireEvent.keyDown(radios[2], { key: 'ArrowRight' })
    expect(onChange).toHaveBeenLastCalledWith('k3')
    fireEvent.keyDown(radios[2], { key: 'End' })
    expect(onChange).toHaveBeenLastCalledWith('k5')
  })

  it('the compact size keeps a one-row radius too', () => {
    render(<Segmented collapse="wrap" size="sm" ariaLabel="Status" options={OPTIONS} value="k0" onChange={() => {}} />)
    expect(screen.getByRole('radiogroup', { name: 'Status' }).className.split(/\s+/)).toContain('rounded-lg')
  })
})
