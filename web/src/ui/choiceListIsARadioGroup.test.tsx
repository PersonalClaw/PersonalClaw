import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { useState } from 'react'
import { ChoiceList, type Choice } from './ChoiceList'

// ── A ChoiceList is a radiogroup: one tab stop, arrows that move and select ────────────────────
//
// Chat preferences' sections are ChoiceLists. Each row is a radio named by its label and
// described by its hint; the group is one tab stop, on the checked row, and the arrows, Home and
// End move and select, skipping a row that cannot be picked now, whose hint says why.

afterEach(() => cleanup())

const OPTIONS: Choice[] = [
  { key: '', label: 'Your default', hint: 'Follows your default.' },
  { key: 'briefly', label: 'Briefly', hint: 'The short answer first.' },
  { key: 'gone', label: 'gone — no longer available', hint: 'Kept until it is back.', disabled: true },
  { key: 'step_by_step', label: 'Step by step', hint: 'Numbered steps.' },
]

function List({ initial = '', onChange = vi.fn(), options = OPTIONS }: { initial?: string; onChange?: (k: string) => void; options?: Choice[] }) {
  const [value, setValue] = useState(initial)
  return (
    <>
      <h2 id="heading">Explanations</h2>
      <ChoiceList labelledBy="heading" options={options} value={value} onChange={(k) => { setValue(k); onChange(k) }} />
    </>
  )
}

const radio = (name: string | RegExp) => screen.getByRole('radio', { name })

describe('a ChoiceList', () => {
  it('is a radiogroup named by its heading, each row a radio named by its label and described by its hint', () => {
    render(<List initial="briefly" />)
    expect(screen.getByRole('radiogroup', { name: 'Explanations' })).toBeTruthy()
    expect(screen.getAllByRole('radio')).toHaveLength(4)
    expect(radio('Briefly').getAttribute('aria-checked')).toBe('true')
    expect(radio('Your default').getAttribute('aria-checked')).toBe('false')
    const hint = document.getElementById(radio('Briefly').getAttribute('aria-describedby') ?? '')
    expect(hint?.textContent).toBe('The short answer first.')
  })

  it('has one tab stop, on the checked row', () => {
    render(<List initial="step_by_step" />)
    const stops = screen.getAllByRole('radio').filter((r) => r.tabIndex === 0)
    expect(stops).toEqual([radio('Step by step')])
  })

  it('puts the tab stop on the first row that can be picked when none is checked', () => {
    render(<List initial="nothing-listed" options={[{ key: 'x', label: 'X', disabled: true }, ...OPTIONS]} />)
    const stops = screen.getAllByRole('radio').filter((r) => r.tabIndex === 0)
    expect(stops).toEqual([radio('Your default')])
  })

  it('arrows move and select, and the focus goes with them', () => {
    const onChange = vi.fn()
    render(<List onChange={onChange} />)
    radio('Your default').focus()
    fireEvent.keyDown(radio('Your default'), { key: 'ArrowDown' })
    expect(onChange).toHaveBeenLastCalledWith('briefly')
    expect(document.activeElement).toBe(radio('Briefly'))
    expect(radio('Briefly').getAttribute('aria-checked')).toBe('true')
    fireEvent.keyDown(radio('Briefly'), { key: 'ArrowUp' })
    expect(onChange).toHaveBeenLastCalledWith('')
    expect(document.activeElement).toBe(radio('Your default'))
  })

  it('Home and End go to the first and the last row that can be picked, past disabled ends', () => {
    const onChange = vi.fn()
    render(<List initial="briefly" onChange={onChange}
      options={[{ key: 'x', label: 'X', disabled: true }, ...OPTIONS, { key: 'y', label: 'Y', disabled: true }]} />)
    fireEvent.keyDown(radio('Briefly'), { key: 'End' })
    expect(onChange).toHaveBeenLastCalledWith('step_by_step')
    fireEvent.keyDown(radio('Step by step'), { key: 'Home' })
    expect(onChange).toHaveBeenLastCalledWith('')
  })

  it('keeps the tab stop on a checked row that cannot be picked, so its reason is read', () => {
    render(<List initial="gone" />)
    expect(radio(/no longer available/).tabIndex).toBe(0)
    expect(screen.getAllByRole('radio').filter((r) => r.tabIndex === 0)).toHaveLength(1)
  })

  it('from a row that cannot be picked, the arrows go to the nearest live row their way', () => {
    const rows: Choice[] = [
      { key: 'a', label: 'A' }, { key: 'b', label: 'B' }, { key: 'gone', label: 'Gone', disabled: true },
      { key: 'c', label: 'C' }, { key: 'd', label: 'D' },
    ]
    const onChange = vi.fn()
    render(<List initial="gone" onChange={onChange} options={rows} />)
    fireEvent.keyDown(radio('Gone'), { key: 'ArrowUp' })
    expect(onChange).toHaveBeenLastCalledWith('b')
    cleanup()
    render(<List initial="gone" onChange={onChange} options={rows} />)
    fireEvent.keyDown(radio('Gone'), { key: 'ArrowDown' })
    expect(onChange).toHaveBeenLastCalledWith('c')
  })

  it('skips a disabled row, which says why and cannot be clicked', () => {
    const onChange = vi.fn()
    render(<List initial="briefly" onChange={onChange} />)
    fireEvent.keyDown(radio('Briefly'), { key: 'ArrowDown' })
    expect(onChange).toHaveBeenLastCalledWith('step_by_step')
    const gone = radio(/no longer available/)
    expect(gone.getAttribute('aria-disabled')).toBe('true')
    expect(document.getElementById(gone.getAttribute('aria-describedby') ?? '')?.textContent).toBe('Kept until it is back.')
    onChange.mockClear()
    fireEvent.click(gone)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('a click picks a row', () => {
    const onChange = vi.fn()
    render(<List onChange={onChange} />)
    fireEvent.click(radio('Step by step'))
    expect(onChange).toHaveBeenCalledWith('step_by_step')
  })
})
