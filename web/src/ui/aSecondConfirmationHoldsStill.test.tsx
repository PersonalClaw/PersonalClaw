// A card's second confirmation keeps one contract (`InlineConfirm`), the inline twin of a
// deliberate dialog: nothing is sent before it, a run of clicks or a held key is never its yes,
// Yes answers only a press begun after a quiet second, and Escape steps back without closing the
// panel behind the card. Folder trust asks so on a card (`TrustFolderButton inline`), and its other
// callers keep their dialog. (The approval's queue form: `theApprovalsQueueFormIsACard.test.tsx`.)
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ARM_MS } from './dialog/DialogShell'
import { InlineConfirm, wrapping } from './InlineConfirm'
import { buttonLook } from './Button'
import { TOUCH_TARGET } from './Toggle'
import { TrustFolderButton } from '../pages/triggers/HeldBackNote'

const trustProjectFolder = vi.fn(async () => ({ dir: '/srv/garden', trusted: true }))
const dialog = vi.fn(async () => true)

vi.mock('../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../lib/api')>()
  return { ...real, api: { ...real.api, trustProjectFolder: (...a: unknown[]) => trustProjectFolder(...(a as [])) } }
})
vi.mock('./dialog', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./dialog')>()),
  confirm: (...a: unknown[]) => dialog(...(a as [])),
}))

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers() })

function ask() {
  const onYes = vi.fn()
  const onBack = vi.fn()
  render(
    <div>
      <button type="button">Allow</button>
      <InlineConfirm sentence="It deletes files. Run it?" verb="run it" onYes={onYes} onBack={onBack} />
    </div>,
  )
  return { onYes, onBack, yes: screen.getByRole('button', { name: 'Yes, run it' }) }
}

/** A pointer press on *el*: its press, then its click. */
function press(el: HTMLElement) {
  fireEvent.pointerDown(el)
  fireEvent.click(el, { detail: 1 })
}

function quiet(ms = ARM_MS) {
  act(() => { vi.advanceTimersByTime(ms) })
}

describe('a second confirmation holds still', () => {
  it('reads Yes with its verb, never the label that opened it, and takes no focus', () => {
    const { yes } = ask()
    expect(yes.textContent).toBe('Yes, run it')
    expect(document.activeElement).not.toBe(yes)
    expect(screen.getByRole('group', { name: 'It deletes files. Run it?' })).toBeTruthy()
  })

  it('answers nothing before it has been quiet a moment, and says why', () => {
    const { yes, onYes } = ask()
    press(yes)
    expect(onYes).not.toHaveBeenCalled()
    quiet(60)
    expect(screen.getByRole('status').textContent).toContain('That did nothing')
  })

  it('answers a press begun after a quiet second', () => {
    const { yes, onYes } = ask()
    quiet()
    press(yes)
    expect(onYes).toHaveBeenCalledTimes(1)
  })

  it('never takes a double click as its yes', () => {
    const { yes, onYes } = ask()
    press(yes)
    quiet(150)
    press(yes)
    expect(onYes).not.toHaveBeenCalled()
  })

  it('starts the wait again on any key or press anywhere', () => {
    const { yes, onYes } = ask()
    quiet(ARM_MS - 100)
    fireEvent.keyDown(window, { key: 'a' })
    quiet(200)
    press(yes)
    expect(onYes).not.toHaveBeenCalled()
    quiet()
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Allow' }))
    press(yes)
    expect(onYes).not.toHaveBeenCalled()
  })

  it('answers a key only from a Yes that has held the focus a quiet second', () => {
    const { yes, onYes } = ask()
    quiet()
    act(() => { yes.focus() })
    fireEvent.click(yes, { detail: 0 })
    expect(onYes).not.toHaveBeenCalled()
    quiet()
    fireEvent.click(yes, { detail: 0 })
    expect(onYes).toHaveBeenCalledTimes(1)
  })

  it('takes ⌘Enter as a chord: the ⌘ pressed first starts no wait again', () => {
    const { yes, onYes } = ask()
    quiet()
    act(() => { yes.focus() })
    quiet()
    fireEvent.keyDown(yes, { key: 'Meta', metaKey: true })
    fireEvent.keyDown(yes, { key: 'Enter', metaKey: true })
    expect(onYes).toHaveBeenCalledTimes(1)
  })

  it('takes ⌘Enter only from Yes', () => {
    const { yes, onYes } = ask()
    quiet()
    fireEvent.keyDown(screen.getByRole('button', { name: 'Back' }), { key: 'Enter', metaKey: true })
    expect(onYes).not.toHaveBeenCalled()
    act(() => { yes.focus() })
    quiet()
    fireEvent.keyDown(yes, { key: 'Enter', metaKey: true })
    expect(onYes).toHaveBeenCalledTimes(1)
  })

  it('steps back on Escape and on Back, and the Escape goes no further', () => {
    const { yes, onBack } = ask()
    const behind = vi.fn()
    window.addEventListener('keydown', (e) => { if (!e.defaultPrevented) behind() })
    fireEvent.keyDown(yes, { key: 'Escape' })
    expect(onBack).toHaveBeenCalledTimes(1)
    expect(behind).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(onBack).toHaveBeenCalledTimes(2)
  })

  it('gives both answers a touch target and an edge forced colours draw, and lets their words wrap', () => {
    ask()
    for (const name of ['Yes, run it', 'Back']) {
      const classes = screen.getByRole('button', { name }).className.split(/\s+/)
      for (const cls of TOUCH_TARGET.split(' ')) expect(classes).toContain(cls)
      expect(classes).toContain('border-transparent')
      expect(classes).toContain('whitespace-normal')
      expect(classes).not.toContain('whitespace-nowrap')
    }
    const look = wrapping(buttonLook('secondary', 'xs', 'pill')).className.split(/\s+/)
    expect(look).toContain('min-h-7')
    expect(look).not.toContain('h-7')
  })
})

describe('folder trust asks once more on a card', () => {
  beforeEach(() => { trustProjectFolder.mockClear(); dialog.mockClear() })

  function card() {
    const onTrusted = vi.fn()
    render(<TrustFolderButton folder="/srv/garden" onTrusted={onTrusted} inline />)
    return { onTrusted, trust: screen.getByRole('button', { name: 'Trust this folder' }) }
  }

  it('asks inline under the button, which never moves, and every press of it only asks', () => {
    const { trust } = card()
    fireEvent.click(trust)
    fireEvent.click(trust)
    expect(screen.getAllByRole('group', { name: /Trust \/srv\/garden\?$/ })).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Trust this folder' })).toBe(trust)
    expect(trust.compareDocumentPosition(screen.getByRole('button', { name: 'Yes, trust it' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    for (const cls of TOUCH_TARGET.split(' ')) expect(trust.className.split(/\s+/)).toContain(cls)
    expect(dialog).not.toHaveBeenCalled()
    expect(trustProjectFolder).not.toHaveBeenCalled()
  })

  it('trusts the folder on a Yes begun after a quiet second, and never on a double click', async () => {
    const { trust, onTrusted } = card()
    fireEvent.click(trust)
    const yes = screen.getByRole('button', { name: 'Yes, trust it' })
    press(yes)
    quiet(150)
    press(yes)
    expect(trustProjectFolder).not.toHaveBeenCalled()
    quiet()
    press(yes)
    await act(async () => {})
    expect(trustProjectFolder).toHaveBeenCalledTimes(1)
    expect(trustProjectFolder).toHaveBeenCalledWith('/srv/garden')
    expect(onTrusted).toHaveBeenCalledTimes(1)
  })

  it('steps back on Back and on Escape, trusting nothing', () => {
    const { trust } = card()
    fireEvent.click(trust)
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.queryByRole('button', { name: 'Yes, trust it' })).toBeNull()
    fireEvent.click(trust)
    fireEvent.keyDown(screen.getByRole('button', { name: 'Yes, trust it' }), { key: 'Escape' })
    expect(screen.queryByRole('button', { name: 'Yes, trust it' })).toBeNull()
    expect(trustProjectFolder).not.toHaveBeenCalled()
  })

  it('keeps its dialog for every caller that does not pass inline', async () => {
    const onTrusted = vi.fn()
    render(<TrustFolderButton folder="/srv/garden" onTrusted={onTrusted} />)
    fireEvent.click(screen.getByRole('button', { name: 'Trust this folder' }))
    await act(async () => {})
    expect(dialog).toHaveBeenCalledTimes(1)
    expect(document.querySelector('[data-inline-confirm]')).toBeNull()
    expect(trustProjectFolder).toHaveBeenCalledTimes(1)
  })
})
