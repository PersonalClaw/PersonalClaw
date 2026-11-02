/** The PersonalClaw icon and name are Home's link, Home opens on Overview, and Attention opens only
 *  when it is asked for (#5880).
 *
 *  The sidebar spent a row on Home while the icon and name at its top only labelled the app, and Home
 *  opened on Attention, so Overview took a switch every time. Now the icon and name are the rail's
 *  first control, named for Home, and open Home on Overview; there is no Home row. `#/dashboard`
 *  shows Overview. Attention opens from what asks for it by name: the shell's needs-you shortcut,
 *  `#/mission-control`, and `?view=attention`. Home's own View switch still moves between the two,
 *  and the address follows it.
 *
 *  The real shell is booted as `main.tsx` boots it, on a set-up home whose reads are still on their
 *  way, at each address, and asserted on what a person sees: the view Home's switch has checked, the
 *  sidebar's rows and the one that is lit, and the address the bar holds.
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
  await Promise.all([import('../pages/dashboard/Home'), import('../pages/ChatPage')])
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
    const now = `${location.hash}|${homeView()}|${document.querySelector('main h1')?.textContent ?? ''}`
    still = now === last && document.querySelector('main') ? still + 1 : 0
    last = now
  }
}

/** The view Home's View switch has checked, or '' where Home is not drawn. */
function homeView(): string {
  const group = screen.queryByRole('radiogroup', { name: 'View' })
  if (!group) return ''
  return within(group).getAllByRole('radio').filter((r) => r.getAttribute('aria-checked') === 'true').map((r) => r.textContent).join(', ')
}
const rail = () => document.querySelector('nav[data-tour="rail"]') as HTMLElement
const railButtons = () => within(rail()).getAllByRole('button')
const railLit = () => railButtons().filter((b) => b.getAttribute('aria-current') === 'page').map((b) => b.getAttribute('aria-label'))

describe('Home opens on Overview, and Attention when it is asked for', () => {
  it.each([
    ['dashboard', 'Overview'],
    ['dashboard?view=overview', 'Overview'],
    ['dashboard?view=attention', 'Attention'],
    ['mission-control', 'Attention'],
    ['mission-control?view=overview', 'Overview'],
  ])('#/%s shows %s', async (address, view) => {
    await open(address)
    expect(homeView()).toBe(view)
  })

  it('Home\'s View switch moves between the two, and the address follows it', async () => {
    await open('dashboard')
    fireEvent.click(within(screen.getByRole('radiogroup', { name: 'View' })).getByRole('radio', { name: /Attention/ }))
    await settle()
    expect(homeView()).toBe('Attention')
    expect(location.hash).toBe('#/dashboard?view=attention')
    fireEvent.click(within(screen.getByRole('radiogroup', { name: 'View' })).getByRole('radio', { name: /Overview/ }))
    await settle()
    expect(homeView()).toBe('Overview')
    expect(location.hash).toBe('#/dashboard')
  })
})

describe('the icon and name are Home\'s link', () => {
  it('the rail\'s first control is named for Home, and the rail has no Home row', async () => {
    await open('chat')
    const [first] = railButtons()
    expect(first.getAttribute('aria-label')).toBe('PersonalClaw, Home')
    expect(railButtons().filter((b) => b.getAttribute('aria-label') === 'Home'), 'no Home row').toHaveLength(0)
  })

  it('opens Home on Overview, and is lit while Home is open', async () => {
    await open('chat')
    fireEvent.click(railButtons()[0])
    await settle()
    expect(location.hash).toBe('#/dashboard')
    expect(homeView()).toBe('Overview')
    expect(railLit()).toEqual(['PersonalClaw, Home'])
    await open('dashboard?view=attention')
    expect(railLit(), 'Home is the page in either view').toEqual(['PersonalClaw, Home'])
  })

  it('on the collapsed rail, the icon alone does the same, named Home', async () => {
    localStorage.setItem('nav-collapsed', '1') // the shell's own key for a collapsed rail
    await open('chat')
    const [icon] = railButtons()
    expect(icon.getAttribute('aria-label')).toBe('Home')
    fireEvent.click(icon)
    await settle()
    expect(location.hash).toBe('#/dashboard')
    expect(homeView()).toBe('Overview')
  })

  it('the palette still goes to Home, and #/dashboard still opens', async () => {
    await open('chat')
    expect(rows.find((r) => r.id === 'go:dashboard')?.label).toBe('Home')
    await open('dashboard')
    expect(location.hash).toBe('#/dashboard')
  })
})
