/** A `deliberate` confirm answers only a press that begins after the dialog has been quiet.
 *
 *  The ask a frame's own content can raise ("A widget asks to send this") is such a confirm.
 *  Confirm is aria-disabled while it waits, so it stays in the Tab order. Every key and press
 *  restarts the quiet, and so does focus reaching Confirm; a key or press on Confirm begun before
 *  it armed never answers; and a keyboard press is read at its click, which counts only when
 *  Confirm has held focus that long with no other key. So typing that runs on into the dialog, then Tab and Enter, a held Space, or a
 *  run of clicks on Confirm's spot is never its yes.
 *
 *  Measured from when the sheet counts as shown (SHOWN_BY_MS at the latest), on fake timers. A
 *  pointer click is `detail: 1`, as a browser's is; a keyboard's is `detail: 0`. */
import { render, act, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { DialogHost } from './DialogHost'
import { ARM_MS, SHOWN_BY_MS } from './DialogShell'
import { closeDialog, confirm, subscribeDialogs } from './dialogStore'

function openDialogs(): { id: number }[] {
  let seen: { id: number }[] = []
  subscribeDialogs((list) => { seen = list as typeof seen })()
  return seen
}

const at = (ms: number) => act(() => { vi.advanceTimersByTime(ms) })

/** Open a deliberate confirm and wait until its sheet counts as shown: t = 0 from here. */
async function ask() {
  render(<DialogHost />)
  const answers: boolean[] = []
  let pending = true
  act(() => {
    void confirm({ title: 'A widget asks to send this', body: 'the text', confirmLabel: 'Send', cancelLabel: 'Don’t send', deliberate: true })
      .then((yes) => { answers.push(yes); pending = false })
  })
  at(SHOWN_BY_MS)
  const send = screen.getByRole('button', { name: /^Send$/ })
  const cancel = screen.getByRole('button', { name: /^Don’t send$/ })
  return { send, cancel, answers, open: () => pending }
}

async function settle() { await act(async () => { await Promise.resolve() }) }

/** A browser's pointer press and its click. */
function pointerClick(el: Element) {
  fireEvent.pointerDown(el)
  fireEvent.click(el, { detail: 1 })
}

beforeEach(() => { vi.useFakeTimers() })
afterEach(async () => {
  act(() => { for (const d of openDialogs()) closeDialog(d.id, false) })
  await settle()
  vi.useRealTimers()
})

describe('a deliberate confirm', () => {
  it('🔴 takes no Tab, a letter, then a Space a moment later as a yes', async () => {
    const { send, cancel, answers, open } = await ask()
    expect(document.activeElement).toBe(cancel)
    fireEvent.keyDown(cancel, { key: 'Tab' })
    act(() => { send.focus() })
    await at(1400)
    fireEvent.keyDown(send, { key: 'x' })
    await at(200)
    fireEvent.keyDown(send, { key: ' ' })
    fireEvent.keyUp(send, { key: ' ' })
    fireEvent.click(send, { detail: 0 })
    await settle()
    expect(answers).toEqual([])
    expect(open()).toBe(true)
  })

  it('🔴 takes no run of clicks on Confirm, and answers a click after a quiet ARM_MS', async () => {
    const { send, answers } = await ask()
    for (let t = 0; t < 3000; t += 300) {
      pointerClick(send)
      await at(300)
    }
    await settle()
    expect(answers, 'every click came less than ARM_MS after the last').toEqual([])
    await at(ARM_MS + 100)
    pointerClick(send)
    await settle()
    expect(answers).toEqual([true])
  })

  it('🔴 takes no Space held across the arming moment', async () => {
    const { send, answers } = await ask()
    await at(500)
    act(() => { send.focus() })
    await at(700)
    fireEvent.keyDown(send, { key: ' ' })
    for (let t = 0; t < 1300; t += 100) {
      await at(100)
      fireEvent.keyDown(send, { key: ' ', repeat: true })
    }
    fireEvent.keyUp(send, { key: ' ' })
    fireEvent.click(send, { detail: 0 })
    await settle()
    expect(answers).toEqual([])
  })

  it('🔴 takes no keyboard press the moment focus reaches Confirm, and one after a quiet ARM_MS', async () => {
    const { send, answers, open } = await ask()
    await at(ARM_MS + 100)
    act(() => { send.focus() })
    fireEvent.keyDown(send, { key: 'Enter' })
    fireEvent.click(send, { detail: 0 })
    await settle()
    expect(open(), 'focus had only just reached Confirm').toBe(true)
    await at(ARM_MS + 100)
    fireEvent.keyDown(send, { key: 'Enter' })
    fireEvent.click(send, { detail: 0 })
    await settle()
    expect(answers).toEqual([true])
  })

  it('takes no pointer press the moment focus reaches Confirm by another way than that press', async () => {
    // Focus moved to Confirm with no key and no press of its own (a screen reader, a script of the
    // page's): the quiet starts again, for a pointer press too.
    const { send, answers } = await ask()
    await at(ARM_MS + 100)
    act(() => { send.focus() })
    pointerClick(send)
    await settle()
    expect(answers).toEqual([])
    await at(ARM_MS + 100)
    pointerClick(send)
    await settle()
    expect(answers).toEqual([true])
  })

  it('takes no keyboard press on Confirm while focus is elsewhere', async () => {
    const { send, answers } = await ask()
    await at(ARM_MS + 100)
    fireEvent.click(send, { detail: 0 })
    await settle()
    expect(answers).toEqual([])
  })

  it('answers a pointer click once armed, with the focus the press moves there its own', async () => {
    const { send, answers } = await ask()
    await at(ARM_MS + 100)
    fireEvent.pointerDown(send)
    act(() => { send.focus() })
    fireEvent.click(send, { detail: 1 })
    await settle()
    expect(answers).toEqual([true])
  })

  it('a press blocked early does not eat a later deliberate yes', async () => {
    const { send, answers } = await ask()
    pointerClick(send)
    await at(ARM_MS + 100)
    pointerClick(send)
    await settle()
    expect(answers).toEqual([true])
  })

  it('arms again on a visibility change', async () => {
    const { send, answers } = await ask()
    await at(ARM_MS + 100)
    act(() => { document.dispatchEvent(new Event('visibilitychange')) })
    pointerClick(send)
    await settle()
    expect(answers).toEqual([])
    await at(ARM_MS + 100)
    pointerClick(send)
    await settle()
    expect(answers).toEqual([true])
  })

  it.each([['mid-quiet', 0], ['armed', ARM_MS + 100]] as const)('keeps "Don’t send" a no %s', async (_, wait) => {
    const { cancel, answers } = await ask()
    await at(wait)
    pointerClick(cancel)
    await settle()
    expect(answers).toEqual([false])
  })

  it.each([['mid-quiet', 0], ['armed', ARM_MS + 100]] as const)('keeps Esc a no %s', async (_, wait) => {
    const { answers } = await ask()
    await at(wait)
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    await settle()
    expect(answers).toEqual([false])
  })

  it('🔴 takes no Space begun during the entrance as a yes, held or not', async () => {
    // Its own render: the press comes before the sheet counts as shown.
    render(<DialogHost />)
    const answers: boolean[] = []
    act(() => {
      void confirm({ title: 'A widget asks to send this', body: 'the text', confirmLabel: 'Send', cancelLabel: 'Don’t send', deliberate: true })
        .then((yes) => { answers.push(yes) })
    })
    const send = screen.getByRole('button', { name: /^Send$/ })
    const cancel = screen.getByRole('button', { name: /^Don’t send$/ })
    await at(100)
    fireEvent.keyDown(cancel, { key: 'Tab' })
    act(() => { send.focus() })
    await at(100)
    fireEvent.keyDown(send, { key: ' ' })
    // Shown at SHOWN_BY_MS (React runs what that timer set when this step ends), then armed.
    await at(SHOWN_BY_MS - 200)
    await at(ARM_MS + 100)
    expect(send.getAttribute('aria-disabled'), 'premise: armed').toBeNull()
    fireEvent.keyUp(send, { key: ' ' })
    fireEvent.click(send, { detail: 0 })
    await settle()
    expect(answers).toEqual([])
    expect(screen.getByTestId('dialog-refused').textContent).toBe('That did nothing: this answers once the dialog has been still a moment.')
  })

  it('🔴 says why a keyboard click on Confirm did nothing, and says it again for the next one', async () => {
    // Focus reaching an armed Confirm starts the quiet again, so its click is refused, and the
    // line says why each time.
    const { send, answers } = await ask()
    await at(ARM_MS + 100)
    act(() => { send.focus() })
    const line = screen.getByTestId('dialog-refused')
    let changes = 0
    const seen = new MutationObserver((records) => { changes += records.length })
    seen.observe(line, { childList: true, characterData: true, subtree: true })
    fireEvent.click(send, { detail: 0 })
    await settle()
    expect(answers).toEqual([])
    expect(line.textContent).toBe('That did nothing: this answers once the dialog has been still a moment.')
    fireEvent.click(send, { detail: 0 })
    await at(100)
    await settle()
    seen.disconnect()
    expect(answers).toEqual([])
    expect(changes, 'set, then cleared and set again').toBeGreaterThanOrEqual(3)
    expect(line.textContent).toBe('That did nothing: this answers once the dialog has been still a moment.')
    // Once Confirm arms, the line clears.
    await at(ARM_MS + 100)
    expect(line.textContent).toBe('')
  })

  it('leaves nothing of an armed press a pan cancelled', async () => {
    const { send, answers } = await ask()
    await at(ARM_MS + 100)
    fireEvent.pointerDown(send)
    fireEvent.pointerCancel(send)
    act(() => { send.focus() })
    pointerClick(send)
    await settle()
    expect(answers).toEqual([])
  })

  it('starts the quiet again on each repeat of a key held on an armed Confirm', async () => {
    const { send, answers } = await ask()
    act(() => { send.focus() })
    await at(ARM_MS + 100)
    expect(send.getAttribute('aria-disabled'), 'premise: armed').toBeNull()
    fireEvent.keyDown(send, { key: ' ' })
    for (let t = 0; t < 1200; t += 100) {
      await at(100)
      fireEvent.keyDown(send, { key: ' ', repeat: true })
    }
    fireEvent.keyUp(send, { key: ' ' })
    fireEvent.click(send, { detail: 0 })
    pointerClick(send)
    await settle()
    expect(answers).toEqual([])
  })

  it('🔴 takes no Space begun on Confirm before it armed, with no repeats, as a yes', async () => {
    const { send, answers } = await ask()
    await at(500)
    act(() => { send.focus() })
    await at(700)
    fireEvent.keyDown(send, { key: ' ' })
    await at(1300)
    expect(send.getAttribute('aria-disabled'), 'premise: armed again since that key').toBeNull()
    fireEvent.keyUp(send, { key: ' ' })
    fireEvent.click(send, { detail: 0 })
    await settle()
    expect(answers).toEqual([])
  })

  it('🔴 starts the quiet again for a key pressed anywhere in the dialog once it armed', async () => {
    const { send, cancel, answers } = await ask()
    await at(ARM_MS + 100)
    fireEvent.keyDown(cancel, { key: 'x' })
    await at(200)
    pointerClick(send)
    await settle()
    expect(answers).toEqual([])
  })

  it('🔴 leaves nothing of a press on an armed Confirm that never clicked', async () => {
    // A right-button press focuses Confirm with no click; focus leaves and a script puts it back.
    const { send, answers } = await ask()
    await at(ARM_MS + 100)
    fireEvent.pointerDown(send, { button: 2 })
    act(() => { send.focus() })
    act(() => { send.blur() })
    act(() => { send.focus() })
    pointerClick(send)
    await settle()
    expect(answers).toEqual([])
  })

  it('🔴 leaves nothing of a press on an armed Confirm that already held focus, once focus leaves it', async () => {
    // The press moves no focus, so only the blur can clear it before a script puts focus back.
    const { send, answers } = await ask()
    act(() => { send.focus() })
    await at(ARM_MS + 100)
    fireEvent.pointerDown(send, { button: 2 })
    act(() => { send.blur() })
    act(() => { send.focus() })
    pointerClick(send)
    await settle()
    expect(answers).toEqual([])
  })

  it('🔴 counts a refused keyboard press as a key: the quiet starts again from it', async () => {
    const { send, answers } = await ask()
    await at(ARM_MS + 100)
    // A pointer press that focuses Confirm and never clicks.
    fireEvent.pointerDown(send)
    act(() => { send.focus() })
    await at(200)
    fireEvent.keyDown(send, { key: 'Enter' })
    fireEvent.click(send, { detail: 0 })
    await at(850)
    fireEvent.keyDown(send, { key: 'Enter' })
    fireEvent.click(send, { detail: 0 })
    await settle()
    expect(answers).toEqual([])
    await at(ARM_MS + 50)
    fireEvent.keyDown(send, { key: 'Enter' })
    fireEvent.click(send, { detail: 0 })
    await settle()
    expect(answers).toEqual([true])
  })

  it('answers a tap once armed, in a touch screen\'s order: press, release, focus, then the click', async () => {
    const { send, answers } = await ask()
    await at(ARM_MS + 100)
    fireEvent.pointerDown(send)
    fireEvent.pointerUp(send)
    act(() => { send.focus() })
    fireEvent.click(send, { detail: 1 })
    await settle()
    expect(answers).toEqual([true])
  })
})

describe('a confirm that is not deliberate', () => {
  it('answers a click at once, as before', async () => {
    render(<DialogHost />)
    const answers: boolean[] = []
    act(() => { void confirm({ title: 'Archive it?', confirmLabel: 'Archive' }).then((y) => { answers.push(y) }) })
    fireEvent.click(screen.getByRole('button', { name: /^Archive$/ }))
    await settle()
    expect(answers).toEqual([true])
  })
})
