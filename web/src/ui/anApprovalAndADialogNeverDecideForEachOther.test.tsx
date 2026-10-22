/** An approval and a dialog never decide for each other (#3822's nested-dialog consent).
 *
 *  The approved keyboard model: no approval shortcut runs from a hidden surface or an unfocused
 *  card, and Escape closes or cancels the current detail without deciding. Two ways a dialog and an
 *  approval card share the screen, and neither may turn a key into a decision:
 *
 *  - **An approval under an open dialog.** A modal dialog covers the page; the card behind it is
 *    out of reach, even while the browser still reports focus on it (a dialog that did not take
 *    focus, a key that raced the dialog's focus trap). ⌘Enter, Enter and Escape on the card decide
 *    nothing until the dialog closes. A popover that is not modal leaves the card answerable.
 *  - **A dialog over a focused approval**, opened from inside the card. Its keys bubble to the card
 *    through React, but a key pressed in the dialog belongs to the dialog: ⌘Enter, Enter and Escape
 *    there decide nothing, and Escape there leaves the card's second confirmation as it was.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createPortal } from 'react-dom'
import { useState } from 'react'
import { Ban, Check } from 'lucide-react'
import { ApprovalPrompt, type ApprovalChoice } from './ApprovalPrompt'
import { Modal } from './Modal'

afterEach(cleanup)

function choices(approve: () => void, deny: () => void): ApprovalChoice[] {
  return [
    { key: 'allow', label: 'Allow', icon: Check, approves: true, onClick: approve },
    { key: 'deny', label: 'Deny', icon: Ban, tone: 'danger', onClick: deny },
  ]
}

const group = () => screen.getByRole('group', { name: 'Permission needed to run bash' })
const allow = () => screen.getByRole('button', { name: /^Allow/ })
const press = (el: Element, init: KeyboardEventInit) => fireEvent.keyDown(el, init)
const EVERY_KEY: KeyboardEventInit[] = [
  { key: 'Enter', metaKey: true },
  { key: 'Enter', ctrlKey: true },
  { key: 'Enter' },
  { key: 'Escape' },
]

/** A card and, beside it on the page, the app's modal dialog, open while `open` is. */
function CardUnderAModal({ approve, deny, startOpen = true, confirms = [] as readonly string[] }: {
  approve: () => void; deny: () => void; startOpen?: boolean; confirms?: readonly string[]
}) {
  const [open, setOpen] = useState(startOpen)
  return (
    <>
      <ApprovalPrompt tool="bash" args="rm -rf build" choices={choices(approve, deny)} confirms={confirms} />
      <button type="button" onClick={() => setOpen((v) => !v)}>Toggle the dialog</button>
      {open && <Modal title="Settings" onClose={() => setOpen(false)}><button type="button">Inside the dialog</button></Modal>}
    </>
  )
}

describe('an approval under an open dialog', () => {
  it('decides nothing on ⌘Enter, Enter or Escape, pressed on the card itself', () => {
    const approve = vi.fn(), deny = vi.fn()
    render(<CardUnderAModal approve={approve} deny={deny} />)
    expect(screen.getByRole('dialog', { name: 'Settings' })).toBeTruthy()
    for (const target of [group(), allow()]) {
      for (const key of EVERY_KEY.slice(0, 3)) press(target, key)
    }
    // Escape last: it closes the dialog, the detail on top, and decides nothing on the card.
    press(group(), { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Settings' })).toBeNull()
    expect(approve).not.toHaveBeenCalled()
    expect(deny).not.toHaveBeenCalled()
  })

  it('decides nothing from inside the dialog either', () => {
    const approve = vi.fn(), deny = vi.fn()
    render(<CardUnderAModal approve={approve} deny={deny} />)
    const inside = screen.getByRole('button', { name: 'Inside the dialog' })
    for (const key of EVERY_KEY.slice(0, 3)) press(inside, key)
    expect(approve).not.toHaveBeenCalled()
    expect(deny).not.toHaveBeenCalled()
  })

  it('does not take the confirm of a request that asks one, behind the dialog', () => {
    const approve = vi.fn(), deny = vi.fn()
    render(<CardUnderAModal approve={approve} deny={deny} confirms={['deletes']} />)
    press(group(), { key: 'Enter', metaKey: true })
    expect(screen.queryByRole('button', { name: /Yes, run it/ }), 'no confirm armed under the dialog').toBeNull()
    expect(approve).not.toHaveBeenCalled()
  })

  it('answers again once the dialog closes, one request for one press', () => {
    const approve = vi.fn(), deny = vi.fn()
    render(<CardUnderAModal approve={approve} deny={deny} />)
    press(group(), { key: 'Enter', metaKey: true })
    expect(approve).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog', { name: 'Settings' })).toBeNull()
    group().focus()
    press(group(), { key: 'Enter', metaKey: true })
    expect(approve).toHaveBeenCalledTimes(1)
  })

  it('stays answerable beside a popover that is not modal', () => {
    const approve = vi.fn(), deny = vi.fn()
    render(
      <>
        <ApprovalPrompt tool="bash" args="rm -rf build" choices={choices(approve, deny)} />
        <div role="dialog" aria-label="A menu beside it"><button type="button">An item</button></div>
      </>,
    )
    group().focus()
    press(group(), { key: 'Enter', metaKey: true })
    expect(approve).toHaveBeenCalledTimes(1)
  })
})

describe('a dialog over a focused approval, opened from inside it', () => {
  function cardWithItsDialog(confirms: readonly string[] = []) {
    const approve = vi.fn(), deny = vi.fn()
    render(
      <ApprovalPrompt tool="bash" args="rm -rf build" choices={choices(approve, deny)} confirms={confirms}
        meta={createPortal(
          <div role="dialog" aria-modal="true" aria-label="The whole command"><button type="button">Copy</button></div>,
          document.body,
        )} />,
    )
    return { approve, deny, inside: screen.getByRole('button', { name: 'Copy' }) }
  }

  it('decides nothing on ⌘Enter, Enter or Escape pressed in the dialog', () => {
    const { approve, deny, inside } = cardWithItsDialog()
    inside.focus()
    for (const key of EVERY_KEY) press(inside, key)
    expect(approve).not.toHaveBeenCalled()
    expect(deny).not.toHaveBeenCalled()
  })

  it('leaves the card\'s second confirmation as it was when Escape closes the dialog\'s detail', () => {
    const { approve, deny, inside } = cardWithItsDialog(['deletes'])
    fireEvent.click(allow())
    expect(screen.getByRole('button', { name: /Yes, run it/ })).toBeTruthy()
    inside.focus()
    press(inside, { key: 'Escape' })
    expect(screen.getByRole('button', { name: /Yes, run it/ }), 'the confirm is the card\'s, not the dialog\'s').toBeTruthy()
    press(inside, { key: 'Enter', metaKey: true })
    expect(approve).not.toHaveBeenCalled()
    expect(deny).not.toHaveBeenCalled()
  })
})
