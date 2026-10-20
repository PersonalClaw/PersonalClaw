import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ErrorBoundary, WidgetBoundary } from './ErrorBoundary'

// ── One widget's throw stays in that widget's place ──────────────────────────────────────────────
//
// A throw while rendering unwinds to the nearest boundary. The shell had none above its corner
// widgets, so the System status popover throwing on one poll took React's whole tree down to an
// empty body. `WidgetBoundary` is that nearest boundary for a widget: a one-line notice with Retry
// in its place, the error logged under the widget's name, and everything outside it untouched.

const probe: { throwing: boolean; error: Error } = { throwing: true, error: new TypeError('unset') }

function Flaky() {
  if (probe.throwing) throw probe.error
  return <p>the readings</p>
}

let consoleError: MockInstance<typeof console.error>
beforeEach(() => {
  probe.throwing = true
  probe.error = new TypeError("Cannot read properties of undefined (reading 'toFixed')")
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
})
afterEach(() => { consoleError.mockRestore(); sessionStorage.clear() })

describe('WidgetBoundary', () => {
  it('🔴 puts a notice with Retry in the widget’s place and leaves its neighbours rendered', () => {
    render(
      <div>
        <p>a neighbour</p>
        <WidgetBoundary what="the system readings"><Flaky /></WidgetBoundary>
        <button type="button">Restart</button>
      </div>,
    )
    const notice = screen.getByRole('alert')
    expect(notice.textContent).toContain("Couldn't show the system readings.")
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
    expect(screen.getByText('a neighbour')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Restart' })).toBeTruthy()
  })

  it('logs the error under the widget’s name', () => {
    render(<WidgetBoundary what="the system readings"><Flaky /></WidgetBoundary>)
    const ours = consoleError.mock.calls.find((call) => call[0] === '[ui] the system readings failed to render')
    expect(ours, 'the boundary must log the throw, naming the widget').toBeTruthy()
    expect(ours?.[1]).toBe(probe.error)
  })

  it('Retry renders the widget again', () => {
    render(<WidgetBoundary what="the system readings"><Flaky /></WidgetBoundary>)
    probe.throwing = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(screen.getByText('the readings')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('compact: a glyph and Retry for an icon slot, with the sentence still read to assistive tech', () => {
    render(<WidgetBoundary what="the system status" compact><Flaky /></WidgetBoundary>)
    const sentence = screen.getByText("Couldn't show the system status.")
    expect(sentence.className).toContain('sr-only')
    expect(screen.getByRole('alert').querySelector('svg'), 'the warning glyph shows').not.toBeNull()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
    // And a sighted pointer user gets the sentence on hover.
    expect(screen.getByRole('alert').closest('[title]')?.getAttribute('title')).toBe("Couldn't show the system status.")
  })

  it('hands a stale-bundle failure to the page boundary, which offers the reload', () => {
    // A missing chunk is not the widget's fault, and its Retry would fetch the same missing file.
    probe.error = new TypeError('Failed to fetch dynamically imported module: /assets/Widget-1a2b3c.js')
    // The page boundary reloads at most once per window; mark this window as used so the test
    // reads the fallback instead of navigating.
    sessionStorage.setItem('pc:chunk-reload-at', String(Date.now()))
    render(
      <ErrorBoundary>
        <WidgetBoundary what="the system readings"><Flaky /></WidgetBoundary>
      </ErrorBoundary>,
    )
    expect(screen.getByText('A new version is available')).toBeTruthy()
    expect(screen.queryByText(/Couldn't show/)).toBeNull()
  })
})
