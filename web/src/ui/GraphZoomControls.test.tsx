import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import { GraphZoomControls } from './GraphZoomControls'

const classOf = (el: Element | null) => el?.getAttribute('class') ?? ''

describe('GraphZoomControls', () => {
  it('renders the three on-glass controls with byte-identical chrome + a11y names', () => {
    const { container, getByLabelText } = render(
      <GraphZoomControls onZoomIn={() => {}} onZoomOut={() => {}} onReset={() => {}} />,
    )
    // Glass panel wrapper preserved verbatim (pinned bottom-right, blur, surface-high/90).
    const panel = container.firstElementChild
    expect(classOf(panel)).toContain('absolute bottom-3 right-3')
    expect(classOf(panel)).toContain('bg-surface-high/90')
    expect(classOf(panel)).toContain('backdrop-blur')

    const buttons = container.querySelectorAll('button')
    expect(buttons).toHaveLength(3)
    // On-glass chrome (deliberately NOT SquareIconButton — see component note).
    for (const b of buttons) {
      const c = classOf(b)
      expect(c).toContain('grid size-7 place-items-center rounded ')
      expect(c).toContain('text-on-surface-var')
      expect(c).toContain('hover:bg-surface-container')
      expect(c).toContain('hover:text-on-surface')
    }
    // Each control has both a title and an accessible name.
    expect(getByLabelText('Zoom in')).toBeTruthy()
    expect(getByLabelText('Zoom out')).toBeTruthy()
    expect(getByLabelText('Reset view')).toBeTruthy()
  })

  it('wires each button to its own callback', () => {
    const onZoomIn = vi.fn()
    const onZoomOut = vi.fn()
    const onReset = vi.fn()
    const { getByLabelText } = render(
      <GraphZoomControls onZoomIn={onZoomIn} onZoomOut={onZoomOut} onReset={onReset} />,
    )
    getByLabelText('Zoom in').click()
    getByLabelText('Zoom out').click()
    getByLabelText('Reset view').click()
    expect(onZoomIn).toHaveBeenCalledTimes(1)
    expect(onZoomOut).toHaveBeenCalledTimes(1)
    expect(onReset).toHaveBeenCalledTimes(1)
  })
})
