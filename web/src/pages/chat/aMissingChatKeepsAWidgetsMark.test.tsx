/** A draft a missing chat carries to a new one keeps saying it is a widget's message.
 *
 *  "Start a new chat" hands the draft over as `?seed=`. A draft a widget put in the composer goes
 *  with `from_widget=1`, which the new chat's composer reads (useChatComposer's `seedFromWidget`),
 *  so what is sent from it still grants none of its links. Safe in a URL: it can only take a grant
 *  away. */
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { MissingChat } from './MissingChat'

describe('a missing chat\'s "Start a new chat"', () => {
  it('🔴 carries a widget\'s draft marked, and the owner\'s own unmarked', () => {
    // A widget's draft arrives in the new chat still marked, and the owner's own unmarked.
    const navigate = vi.fn()
    const view = render(<MissingChat draft="[UI] open" fromWidget navigate={navigate} />)
    fireEvent.click(screen.getByRole('button', { name: /Start a new chat/ }))
    expect(navigate).toHaveBeenLastCalledWith(`chat/new?seed=${encodeURIComponent('[UI] open')}&from_widget=1`, { replace: true })
    view.unmount()
    render(<MissingChat draft="my own words" navigate={navigate} />)
    fireEvent.click(screen.getByRole('button', { name: /Start a new chat/ }))
    expect(navigate).toHaveBeenLastCalledWith(`chat/new?seed=${encodeURIComponent('my own words')}`, { replace: true })
  })
})
