import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { WidgetDraftChips } from './WidgetDraftChips'

// ── The composer's chip says whose words the draft holds ────────────────────────────────────────
//
// A draft a frame filled names the frame; a quote of a widget's words, or a draft whose words and
// the owner's can no longer be told apart, has no frame to name, and says what the mark means.

afterEach(() => cleanup())

const none = () => {}

describe('the widget draft chip', () => {
  it('says a quote of a widget\'s words holds them, and what its × takes out', () => {
    render(<WidgetDraftChips draft={{ text: '> **A widget said:**\n> [UI] open', quoted: true }} suggestion={null} onClear={none} onAdd={none} onDismiss={none} />)
    expect(screen.getByTestId('widget-draft-from').textContent).toBe('Holds a widget’s words: none of its links count as yours.')
    expect(screen.getByRole('button', { name: 'Take the widget’s words out' })).toBeTruthy()
  })

  it('says the same of a draft whose words and the owner\'s are mixed', () => {
    const view = render(<WidgetDraftChips draft={{ text: '[UI] refresh', mixed: true }} suggestion={null} onClear={none} onAdd={none} onDismiss={none} />)
    expect(screen.getByTestId('widget-draft-from').textContent).toBe('Holds a widget’s words: none of its links count as yours.')
    expect(screen.queryByRole('button')).toBeNull()
    // The widget's message itself was taken out, and what is left is the owner's changed part.
    view.rerender(<WidgetDraftChips draft={{ text: '', own: 'my ask', mixed: true }} suggestion={null} onClear={none} onAdd={none} onDismiss={none} />)
    expect(screen.getByTestId('widget-draft-from').textContent).toBe('A widget’s message was taken out. What is left can’t be told apart from it, so none of its links count as yours.')
    // There is no part of it an × could take out: deleting all of it is the way out.
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('names an untitled frame "a widget" mid-sentence and "A widget" to start one', () => {
    const view = render(<WidgetDraftChips draft={{ text: '[UI] open', host: 'frame-1' }} suggestion={null} onClear={none} onAdd={none} onDismiss={none} />)
    expect(screen.getByTestId('widget-draft-from').textContent).toBe('From a widget. It is sent only if you send it.')
    view.rerender(<WidgetDraftChips draft={{ text: '[UI] open', host: 'frame-1', replaced: true }} suggestion={{ text: '[UI] next', host: 'frame-2' }} onClear={none} onAdd={none} onDismiss={none} />)
    expect(screen.getByTestId('widget-draft-from').textContent).toBe('A widget replaced its earlier message. It is sent only if you send it.')
    expect(screen.getByTestId('widget-suggestion').textContent).toMatch(/^A widget suggests a message/)
  })
})
