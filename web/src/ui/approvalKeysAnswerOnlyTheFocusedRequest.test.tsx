/** ⌘Enter (Ctrl+Enter elsewhere) approves the request that has keyboard focus, and nothing else.
 *
 *  A key must never approve something the owner did not see. So the key acts only on the card that
 *  holds focus, in a window that has focus, never from a text field, never on a request that has
 *  ended or is being answered, never on a card that is hidden, and a held or repeated key sends one
 *  request. Enter alone on the card and Escape never decide; Enter or Space on a focused button
 *  presses that button, as a click does. Decline is a button with no key of its own. A delete, a
 *  write outside the workspace, a send to other people, spending, and a call to a tool that says
 *  nothing about what it does each wait for a second confirmation, and nothing is sent before it:
 *  pressing Allow again, by a double-click or a held Enter, only asks again. One request per
 *  decision: once an answer landed the card sends nothing more while it is still on the page. A
 *  request that fails leaves the card answerable. Arrival never moves focus onto an approval action.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createPortal } from 'react-dom'
import { Ban, Check } from 'lucide-react'
import { ApprovalPrompt, CONFIRM_WORDS, confirmSentences, type ApprovalChoice } from './ApprovalPrompt'

afterEach(cleanup)

function card({ approve = vi.fn(), deny = vi.fn(), confirms = [] as readonly string[],
  busy = false, meta = undefined as React.ReactNode, tool = 'bash' } = {}) {
  const choices: ApprovalChoice[] = [
    { key: 'allow', label: 'Allow', icon: Check, approves: true, busy, onClick: approve },
    { key: 'deny', label: 'Deny', icon: Ban, tone: 'danger', busy, onClick: deny },
  ]
  const view = render(<ApprovalPrompt tool={tool} args="rm -rf build" choices={choices}
    confirms={confirms} meta={meta} />)
  return { approve, deny, view, group: screen.getByRole('group', { name: `Permission needed to run ${tool}` }) }
}

const modEnter = (el: Element, over: KeyboardEventInit = {}) =>
  fireEvent.keyDown(el, { key: 'Enter', metaKey: true, ...over })

describe('the approval key', () => {
  it('approves the focused request once on ⌘Enter, and on Ctrl+Enter', () => {
    const { approve, deny, group } = card()
    screen.getByRole('button', { name: /Deny/ }).focus()
    modEnter(document.activeElement!)
    expect(approve).toHaveBeenCalledTimes(1)
    expect(deny).not.toHaveBeenCalled()
    cleanup()
    const second = card()
    second.group.focus()
    fireEvent.keyDown(second.group, { key: 'Enter', ctrlKey: true })
    expect(second.approve).toHaveBeenCalledTimes(1)
    void group
  })

  it('never decides on Enter alone, on Escape, or on any other key', () => {
    const { approve, deny, group } = card()
    group.focus()
    for (const key of ['Enter', 'Escape', ' ', 'a', 'y', 'Tab']) fireEvent.keyDown(group, { key })
    fireEvent.keyDown(group, { key: 'Escape', metaKey: true })
    expect(approve).not.toHaveBeenCalled()
    expect(deny).not.toHaveBeenCalled()
  })

  it('does nothing while focus is elsewhere on the page (an unfocused card)', () => {
    const { approve } = card()
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    outside.focus()
    modEnter(outside)
    modEnter(document.body)
    expect(approve).not.toHaveBeenCalled()
    outside.remove()
  })

  it("does nothing while its window does not have focus, focus still inside the card; with it, approves", () => {
    const { approve, group } = card()
    group.focus()
    const hasFocus = vi.spyOn(document, 'hasFocus').mockReturnValue(false)
    modEnter(group)
    expect(approve, 'another window has focus: the buddy over the app the owner works in').not.toHaveBeenCalled()
    hasFocus.mockReturnValue(true)
    modEnter(group)
    expect(approve).toHaveBeenCalledTimes(1)
    hasFocus.mockRestore()
  })

  it('does nothing from a text field, even one inside the card', () => {
    const { approve } = card({ meta: <textarea aria-label="A note" /> })
    const field = screen.getByRole('textbox', { name: 'A note' })
    field.focus()
    modEnter(field)
    expect(approve).not.toHaveBeenCalled()
  })

  it('does nothing from a dialog opened over the card, even one rendered from inside it', () => {
    const { approve } = card({ meta: createPortal(
      <div role="dialog" aria-modal="true" aria-label="Nested"><button type="button">Inside the dialog</button></div>,
      document.body,
    ) })
    const inside = screen.getByRole('button', { name: 'Inside the dialog' })
    inside.focus()
    modEnter(inside)
    expect(approve).not.toHaveBeenCalled()
  })

  it('does nothing from a one-line field or a picker inside the card', () => {
    const { approve } = card({ meta: <><input type="text" aria-label="A name" /><select aria-label="A scope"><option>once</option></select></> })
    for (const field of [screen.getByRole('textbox', { name: 'A name' }), screen.getByRole('combobox', { name: 'A scope' })]) {
      field.focus()
      modEnter(field)
    }
    expect(approve).not.toHaveBeenCalled()
  })

  it('does nothing from the composer or any editor', () => {
    const { approve } = card({ meta: <div contentEditable suppressContentEditableWarning role="textbox" aria-label="Message input" tabIndex={0} /> })
    const editor = screen.getByRole('textbox', { name: 'Message input' })
    editor.focus()
    modEnter(editor)
    expect(approve).not.toHaveBeenCalled()
  })

  it('does nothing on a request that has ended, or one whose answer is on its way', () => {
    const ended = card()
    ended.group.focus()
    // The request ended while it had focus: its card is gone from the page.
    ended.view.unmount()
    modEnter(ended.group)
    expect(ended.approve).not.toHaveBeenCalled()
    cleanup()
    const busy = card({ busy: true })
    busy.group.focus()
    modEnter(busy.group)
    expect(busy.approve).not.toHaveBeenCalled()
  })

  it('does nothing on a hidden card', () => {
    const { approve, group } = card()
    group.focus()
    group.parentElement!.setAttribute('hidden', '')
    modEnter(group)
    group.parentElement!.removeAttribute('hidden')
    group.parentElement!.setAttribute('inert', '')
    modEnter(group)
    group.parentElement!.removeAttribute('inert')
    group.parentElement!.setAttribute('aria-hidden', 'true')
    modEnter(group)
    expect(approve).not.toHaveBeenCalled()
  })

  it('sends one request for a held or repeated key', () => {
    let settle: (v: unknown) => void = () => {}
    const approve = vi.fn(() => new Promise((r) => { settle = r }))
    const { group } = card({ approve })
    group.focus()
    modEnter(group)
    modEnter(group, { repeat: true })
    modEnter(group)
    expect(approve).toHaveBeenCalledTimes(1)
    act(() => settle(true))
  })

  it('sends nothing more for a key still held after its answer settled', () => {
    const approve = vi.fn()
    const { group } = card({ approve })
    group.focus()
    modEnter(group)
    modEnter(group, { repeat: true })
    modEnter(group, { repeat: true })
    expect(approve).toHaveBeenCalledTimes(1)
  })

  it('sends nothing for a key still held after a failed answer settled', async () => {
    const approve = vi.fn().mockRejectedValueOnce(new Error('the gateway did not respond')).mockResolvedValue(true)
    const { group } = card({ approve })
    group.focus()
    await act(async () => { modEnter(group) })
    // The failure settled, and the key is still down: its repeats are not a fresh decision.
    await act(async () => { modEnter(group, { repeat: true }) })
    expect(approve).toHaveBeenCalledTimes(1)
    await act(async () => { modEnter(group) })
    expect(approve).toHaveBeenCalledTimes(2)
  })

  it('leaves the card answerable when the request fails, whether it rejects or says it did not land', async () => {
    const approve = vi.fn().mockRejectedValueOnce(new Error('the gateway did not respond')).mockResolvedValueOnce(false).mockResolvedValue(true)
    const { group } = card({ approve })
    group.focus()
    await act(async () => { modEnter(group) })
    await act(async () => { modEnter(group) })
    await act(async () => { modEnter(group) })
    expect(approve).toHaveBeenCalledTimes(3)
  })

  it('sends nothing more once an answer landed, while the card is still on the page', async () => {
    const approve = vi.fn().mockResolvedValue(true)
    const deny = vi.fn().mockResolvedValue(true)
    const { group } = card({ approve, deny })
    group.focus()
    await act(async () => { modEnter(group) })
    await act(async () => { modEnter(group) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Allow/ })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Deny/ })) })
    expect(approve).toHaveBeenCalledTimes(1)
    expect(deny).not.toHaveBeenCalled()
  })

  it('sends no approve while a Deny is on its way, and nothing more once it landed', async () => {
    let settle: (v: unknown) => void = () => {}
    const deny = vi.fn(() => new Promise((r) => { settle = r }))
    const { approve, group } = card({ deny })
    fireEvent.click(screen.getByRole('button', { name: /Deny/ }))
    group.focus()
    modEnter(group)
    fireEvent.click(screen.getByRole('button', { name: /Deny/ }))
    expect(approve).not.toHaveBeenCalled()
    expect(deny).toHaveBeenCalledTimes(1)
    await act(async () => { settle(true) })
    modEnter(group)
    expect(approve).not.toHaveBeenCalled()
  })

  it('moves no focus on arrival', () => {
    card()
    expect(document.activeElement).toBe(document.body)
  })

  it('shows its key in the Allow tooltip, and gives Deny none', () => {
    card()
    expect(screen.getByRole('button', { name: /Allow/ }).getAttribute('title')).toMatch(/⌘Enter|Ctrl\+Enter/)
    expect(screen.getByRole('button', { name: /Deny/ }).getAttribute('title') ?? '').not.toMatch(/Enter/)
  })
})

describe('the second confirmation', () => {
  for (const kind of ['deletes', 'outside_workspace', 'sends', 'spends', 'undeclared']) {
    it(`a request that ${kind} sends nothing on the first ⌘Enter or click`, () => {
      const { approve, group } = card({ confirms: [kind] })
      group.focus()
      modEnter(group)
      expect(approve).not.toHaveBeenCalled()
      cleanup()
      const clicked = card({ confirms: [kind] })
      fireEvent.click(screen.getByRole('button', { name: /Allow/ }))
      expect(clicked.approve).not.toHaveBeenCalled()
      expect(screen.getByRole('button', { name: /Yes, run it/ })).toBeTruthy()
    })
  }

  for (const kind of ['deletes', 'outside_workspace', 'sends', 'spends', 'undeclared']) {
    it(`a request that ${kind} sends nothing on a second press of Allow: a double-click or a held Enter only asks again`, () => {
      const { approve } = card({ confirms: [kind] })
      const allow = screen.getByRole('button', { name: /^Allow/ })
      // Enter or Space on a focused button is a click, once per keydown, its auto-repeat included.
      fireEvent.click(allow)
      fireEvent.click(allow)
      fireEvent.click(allow)
      allow.focus()
      modEnter(allow)
      expect(approve).not.toHaveBeenCalled()
      expect(screen.getByRole('button', { name: /Yes, run it/ })).toBeTruthy()
    })
  }

  it('says a kind it does not know as the undeclared one', () => {
    expect(confirmSentences(['bogus'])).toEqual([CONFIRM_WORDS.undeclared])
    card({ confirms: ['bogus'] })
    fireEvent.click(screen.getByRole('button', { name: /^Allow/ }))
    expect(screen.getByText(new RegExp(CONFIRM_WORDS.undeclared.slice(0, 30).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))).toBeTruthy()
  })

  it('asks once more, and its confirm sends exactly one request', () => {
    const { approve, group } = card({ confirms: ['deletes'] })
    group.focus()
    modEnter(group)
    expect(screen.getByText(/deletes files/i)).toBeTruthy()
    // The confirm does not take focus on its own: a held key must not fall through to it.
    expect(document.activeElement).toBe(group)
    modEnter(group, { repeat: true })
    // A fresh press anywhere but the confirm sends nothing either: only the confirm takes the key.
    modEnter(group)
    expect(approve).not.toHaveBeenCalled()
    const yes = screen.getByRole('button', { name: /Yes, run it/ })
    yes.focus()
    modEnter(yes)
    modEnter(yes)
    expect(approve).toHaveBeenCalledTimes(1)
  })

  it('the confirm can be stepped back from with Escape, which decides nothing', () => {
    const { approve, deny, group } = card({ confirms: ['sends'] })
    fireEvent.click(screen.getByRole('button', { name: /Allow/ }))
    fireEvent.keyDown(group, { key: 'Escape' })
    expect(screen.queryByRole('button', { name: /Yes, run it/ })).toBeNull()
    expect(approve).not.toHaveBeenCalled()
    expect(deny).not.toHaveBeenCalled()
  })

  it('a request with no second confirmation decides on the first press', () => {
    const { approve } = card({ confirms: [] })
    fireEvent.click(screen.getByRole('button', { name: /Allow/ }))
    expect(approve).toHaveBeenCalledTimes(1)
  })
})
