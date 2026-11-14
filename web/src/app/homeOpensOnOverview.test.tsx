/** The PersonalClaw icon and name are Home's link, Home is its Overview, and Needs you is a page of
 *  its own (#5880).
 *
 *  The sidebar spent a row on Home while the icon and name at its top only labelled the app, and Home
 *  opened on Attention, so Overview took a switch every time. Now the icon and name are the rail's
 *  first control, named for Home, and open Home; there is no Home row. `#/dashboard` is Home's
 *  Overview, with no View switch. What waits on your answer is Needs you, `#/mission-control`, which
 *  the badge, the band, ⌘⌥A and the palette open; Home stays lit while it shows, since it has no
 *  row of its own. An old `#/dashboard?view=attention` lands on Needs you, its `open` kept, and an
 *  old `#/mission-control?view=overview` on Home, each with a replace.
 *
 *  The real shell is booted as `main.tsx` boots it, on a set-up home whose reads are still on their
 *  way, at each address, and asserted on what a person sees: the page's heading, the sidebar's rows
 *  and the one that is lit, and the address the bar holds.
 */
import { StrictMode } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { Command } from './CommandPalette'

/** The rows the shell last handed the palette. */
let rows: readonly Command[] = []

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {}, refreshKinds: () => [] }))
vi.mock('../ui/DotGlow', () => ({ DotGlow: () => null }))
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const gateway = new Proxy({}, {
    get: (_t, method: string) => () => method === 'dashboardConfig'
      ? Promise.resolve({ user_name: 'Ada', username: 'ada' })
      : new Promise(() => {}),
  })
  return { ...real, api: gateway }
})
vi.mock('./CommandPalette', async (orig) => {
  const real = await orig<typeof import('./CommandPalette')>()
  return { ...real, CommandPalette: ({ commands }: { commands: Command[] }) => { rows = commands; return null } }
})

const { App } = await import('./App')
const { ErrorBoundary } = await import('./ErrorBoundary')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
const { IdentityProvider } = await import('./identity')
const { installAppSdk } = await import('./appSdk')
const { registerBuiltinContentTypes } = await import('../ui/content/registerBuiltins')

installAppSdk()
registerBuiltinContentTypes()

beforeAll(async () => {
  await Promise.all([import('../pages/dashboard/Home'), import('../pages/dashboard/NeedsYouPage'), import('../pages/ChatPage')])
}, 120_000)

beforeEach(() => {
  vi.stubGlobal('fetch', () => new Promise(() => {}))
  localStorage.clear()
  sessionStorage.clear()
  rows = []
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

async function open(address: string) {
  cleanup()
  history.replaceState(null, '', `#/${address}`)
  render(
    <StrictMode>
      <ThemeProvider><AppearanceProvider><PersonalityProvider><IdentityProvider>
        <ErrorBoundary><App /></ErrorBoundary>
      </IdentityProvider></PersonalityProvider></AppearanceProvider></ThemeProvider>
    </StrictMode>,
  )
  await settle()
}

/** Let the shell run until the address and what it draws have held still for a few ticks. */
async function settle() {
  let last = ''
  let still = 0
  for (let tick = 0; tick < 600 && still < 3; tick++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
    const now = `${location.hash}|${page()}`
    still = now === last && document.querySelector('main') ? still + 1 : 0
    last = now
  }
}

/** The page drawn, by its heading: Home's greeting is its Overview. */
function page(): string {
  const h1 = document.querySelector('main h1')?.textContent ?? ''
  return /^Good (morning|afternoon|evening)/.test(h1) ? 'Home' : h1
}
const rail = () => document.querySelector('nav[data-tour="rail"]') as HTMLElement
const railButtons = () => within(rail()).getAllByRole('button')
const railLit = () => railButtons().filter((b) => b.getAttribute('aria-current') === 'page').map((b) => b.getAttribute('aria-label'))

describe('Home is its Overview, and Needs you a page of its own', () => {
  it.each([
    ['dashboard', 'Home', '#/dashboard'],
    ['dashboard?view=attention', 'Needs you', '#/mission-control'],
    ['dashboard?view=attention&open=waiting%3Aapproval%3Aa1', 'Needs you', '#/mission-control?open=waiting%3Aapproval%3Aa1'],
    ['mission-control', 'Needs you', '#/mission-control'],
    ['mission-control?view=overview', 'Home', '#/dashboard'],
  ])('#/%s shows %s at %s', async (address, shown, lands) => {
    await open(address)
    expect(page()).toBe(shown)
    expect(location.hash).toBe(lands)
  })

  it('an old address lands with a replace, so Back never returns to it', async () => {
    await open('chat')
    const before = history.length
    history.pushState(null, '', '#/dashboard?view=attention')
    window.dispatchEvent(new HashChangeEvent('hashchange'))
    await settle()
    expect(location.hash).toBe('#/mission-control')
    expect(history.length, 'the old address was replaced, not pushed over').toBe(before + 1)
  })

  it('no page draws a View switch', async () => {
    for (const address of ['dashboard', 'mission-control']) {
      await open(address)
      expect(screen.queryByRole('radiogroup', { name: 'View' }), address).toBeNull()
    }
  })

  it('Needs you lights Home, since it has no row of its own', async () => {
    await open('mission-control')
    expect(railLit()).toEqual(['PersonalClaw, Home'])
    expect(railButtons().filter((b) => /Needs you|Mission Control|Attention/.test(b.getAttribute('aria-label') ?? '')), 'no row').toHaveLength(0)
  })
})

describe('the icon and name are Home\'s link', () => {
  it('the rail\'s first control is named for Home, and the rail has no Home row', async () => {
    await open('chat')
    const [first] = railButtons()
    expect(first.getAttribute('aria-label')).toBe('PersonalClaw, Home')
    expect(railButtons().filter((b) => b.getAttribute('aria-label') === 'Home'), 'no Home row').toHaveLength(0)
  })

  it('opens Home, and is lit while Home is open', async () => {
    await open('chat')
    fireEvent.click(railButtons()[0])
    await settle()
    expect(location.hash).toBe('#/dashboard')
    expect(page()).toBe('Home')
    expect(railLit()).toEqual(['PersonalClaw, Home'])
  })

  it('on the collapsed rail, the icon alone does the same, named Home', async () => {
    localStorage.setItem('nav-collapsed', '1') // the shell's own key for a collapsed rail
    await open('chat')
    const [icon] = railButtons()
    expect(icon.getAttribute('aria-label')).toBe('Home')
    fireEvent.click(icon)
    await settle()
    expect(location.hash).toBe('#/dashboard')
    expect(page()).toBe('Home')
  })

  it('the palette still goes to Home and to Needs you, and #/dashboard still opens', async () => {
    await open('chat')
    expect(rows.find((r) => r.id === 'go:dashboard')?.label).toBe('Home')
    expect(rows.find((r) => r.id === 'go:mission-control')?.label).toBe('Needs you')
    await open('dashboard')
    expect(location.hash).toBe('#/dashboard')
  })
})
