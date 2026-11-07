import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'
import { useRef } from 'react'
import { SelectionQuote } from './SelectionQuote'

// ── A quote that takes in a widget's words says so, wherever its ends lie ───────────────────────
//
// A selection that runs from the owner's message into a widget's has its common ancestor above
// both turns, so the enclosing turn names neither. The quote still holds the widget's words, and
// the draft it lands in is marked.

// jsdom lays nothing out: a range has no box of its own to measure.
Range.prototype.getBoundingClientRect ??= () => ({ x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) })

afterEach(() => cleanup())

function Harness({ onQuote, widgetWordsIn }: { onQuote: (t: string, a?: string, w?: boolean) => void; widgetWordsIn: (r: Range) => boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  return (
    <div ref={ref} data-testid="root">
      <div data-testid="own">my own words</div>
      <div data-testid="widget">[UI] refresh</div>
      <SelectionQuote scrollRef={ref} onQuote={onQuote} attributionFor={() => undefined} widgetWordsIn={widgetWordsIn} />
    </div>
  )
}

describe('select-to-quote', () => {
  /** Select from the owner's message into the widget's, and press Quote: what onQuote got. */
  function quoteAcross(widget: boolean) {
    const onQuote = vi.fn()
    const widgetWordsIn = vi.fn(() => widget)
    render(<Harness onQuote={onQuote} widgetWordsIn={widgetWordsIn} />)
    const range = document.createRange()
    range.setStart(screen.getByTestId('own').firstChild as Text, 3)
    range.setEnd(screen.getByTestId('widget').firstChild as Text, 4)
    const sel = window.getSelection()
    act(() => { sel?.removeAllRanges(); sel?.addRange(range) })
    act(() => { fireEvent.mouseUp(document) })
    fireEvent.mouseDown(screen.getByRole('button', { name: /Quote/ }))
    expect(widgetWordsIn).toHaveBeenCalled()
    expect(onQuote).toHaveBeenCalledTimes(1)
    return onQuote.mock.calls[0]
  }

  it('🔴 hands on a quote that takes in a widget\'s words as a widget\'s', () => {
    expect(quoteAcross(true)[2]).toBe(true)
  })

  it('hands on a quote of the owner\'s words alone as theirs', () => {
    expect(quoteAcross(false)[2]).toBe(false)
  })
})
