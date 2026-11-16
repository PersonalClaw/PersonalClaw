import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import { chat, libraryItem, libraryRead, mountPage, PID } from '../../test/projectPageHarness'

// ── A project page's menus are sheets on a phone (§1.1 item 16, §8) ────────────────────────────
//
// From 320 to 768 px the header's "…" and its New, About › Knowledge's Add (the library's own menu)
// and the library preview's Use in chat open as phone sheets: a named, modal sheet with 44 px rows,
// the library's form for a menu on a phone. On a wide window each opens as the menu it is.

Element.prototype.scrollIntoView ??= () => {}

function windowOf(width: number) {
  vi.stubGlobal('matchMedia', (query: string) => {
    const max = Number(/max-width:\s*(\d+)px/.exec(query)?.[1] ?? Infinity)
    const min = Number(/min-width:\s*(\d+)px/.exec(query)?.[1] ?? 0)
    return { matches: width <= max && width >= min, media: query, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false }
  })
}

beforeEach(() => { resetDataStore(); localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); resetDataStore() })

const sheet = () => document.querySelector<HTMLElement>('[data-phone-sheet]')
/** Open `trigger` and return the phone sheet it opened: a modal dialog named by it (its label,
 *  else its title, else its text), holding the sheet. */
function openSheet(trigger: HTMLElement): HTMLElement {
  fireEvent.click(trigger)
  const name = trigger.getAttribute('aria-label') || trigger.title || trigger.textContent?.trim() || ''
  const d = screen.getByRole('dialog', { name })
  expect(d.getAttribute('aria-modal'), `${name} opened as a menu, not a sheet`).toBe('true')
  expect(d.closest('[data-phone-sheet]') ?? d.querySelector('[data-phone-sheet]'), `${name} has no sheet`).toBeTruthy()
  return d
}
const LIB = libraryRead([libraryItem('artifact:report-1', 'Spring planting plan'), libraryItem('knowledge:k-1', 'Tomato varieties')])
const close = () => fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })

describe("a project page's menus on a phone", () => {
  it('the header\'s New and Knowledge\'s Add open as sheets at 390 px', async () => {
    windowOf(390)
    mountPage({ sessions: [chat('c-1', 'Seed ideas', PID)], library: LIB })
    expect(within(openSheet(await screen.findByRole('button', { name: 'New' }))).getByRole('menuitem', { name: /^Goal loop/ })).toBeTruthy()
    close()
    expect(within(openSheet(screen.getByRole('button', { name: 'More actions' }))).getByText('Rename')).toBeTruthy()
    close()
    const k = (await screen.findByRole('heading', { level: 3, name: 'Knowledge' })).closest('section')!
    expect(within(openSheet(within(k).getByRole('button', { name: /^Add/ }))).getByRole('menuitem', { name: /^Write a note/ })).toBeTruthy()
  })

  it("the library preview's Use in chat opens as a sheet at 390 px", async () => {
    windowOf(390)
    mountPage({ sessions: [chat('c-1', 'Seed ideas', PID)], library: LIB, query: { open: 'artifact:report-1' } })
    const preview = (await screen.findByRole('heading', { level: 2, name: 'Spring planting plan' })).closest('section')!
    expect(within(openSheet(within(preview).getByRole('button', { name: /^Use in chat/ }))).getByRole('button', { name: /^New chat with this/ })).toBeTruthy()
  })

  it('on a wide window New opens as the menu it is (the control)', async () => {
    windowOf(1280)
    mountPage({ sessions: [chat('c-1', 'Seed ideas', PID)], library: LIB })
    fireEvent.click(await screen.findByRole('button', { name: 'New' }))
    expect(await screen.findByRole('menuitem', { name: /^Goal loop/ })).toBeTruthy()
    expect(sheet()).toBeNull()
  })
})
