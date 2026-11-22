/** A key pressed before a dialog opened does not answer it.
 *
 *  Driven (dogfood, 11-21): Library › New collection asks for a name, then for a search. Enter on
 *  the name made the collection at once, with no search step shown. The Enter that answered the
 *  first prompt went on to the window after the second had opened in its wake (a browser runs the
 *  first prompt's answer before the key reaches the window), and the second prompt's own Enter
 *  handler took it as its answer. A dialog now answers only a key pressed once it is open.
 *
 *  The key here is one already on its way when the dialog opens: sent to the window once the dialog
 *  has opened, with its handlers in place, in the same turn of the event loop. */
import { afterEach, describe, expect, it } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { DialogHost } from './DialogHost'
import { closeDialog, confirm, promptInput, subscribeDialogs } from './dialogStore'

afterEach(() => {
  let open: { id: number }[] = []
  subscribeDialogs((list) => { open = list as typeof open })()
  act(() => { for (const d of open) closeDialog(d.id, null) })
  cleanup()
})

const nextTurn = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })

describe('a dialog and the key that opened it', () => {
  it('🔴 a prompt opened as an Enter is on its way is not answered by it', async () => {
    render(<DialogHost />)
    let answer: Record<string, string> | string | null | undefined
    act(() => {
      void promptInput({ title: 'New collection', label: 'Search', confirmLabel: 'Create collection', required: false })
        .then((v) => { answer = v as typeof answer })
    })
    // Open, its handlers in place, and no turn of the event loop since: the key still on its way.
    act(() => { fireEvent.keyDown(window, { key: 'Enter' }) })
    await nextTurn()

    expect(answer, 'the Enter from before it opened answered it').toBeUndefined()
    expect(screen.getByRole('button', { name: 'Create collection' })).toBeTruthy()
  })

  it('🔴 a confirm opened as an Enter is on its way is not answered by it', async () => {
    render(<DialogHost />)
    let answer: boolean | undefined
    act(() => { void confirm({ title: 'Send it?', confirmLabel: 'Send' }).then((v) => { answer = v }) })
    act(() => { fireEvent.keyDown(window, { key: 'Enter' }) })
    await nextTurn()

    expect(answer).toBeUndefined()
  })

  it('answers an Enter pressed once it is open', async () => {
    render(<DialogHost />)
    let answer: unknown
    act(() => { void promptInput({ title: 'New collection', label: 'Search', required: false }).then((v) => { answer = v }) })
    await nextTurn()

    fireEvent.keyDown(window, { key: 'Enter' })
    await nextTurn()

    expect(answer).not.toBeUndefined()
  })
})
