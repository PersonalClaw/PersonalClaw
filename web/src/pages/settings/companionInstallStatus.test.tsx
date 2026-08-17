import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { CompanionPanel } from './CompanionPanel'

// ── An explanation written to be READ has to reach a reader ────────────────────────────────────────
//
// `serviceWorkerBlockedReason` (MOBILE-COMPANION T3.1) exists to tell a user why install and offline
// are unavailable. Its own docstring makes the argument: *"Saying so out loud beats an install button
// that silently never appears."* It was tested, it was correct, and its ONLY consumer was
// `console.info` — so the sentence reached nobody. Censused before this change: zero production
// importers outside `registerServiceWorker.ts` itself.
//
// 🔑 WHY IT MATTERS IN PRACTICE, not in theory. A service worker requires a secure context, and the
// documented way to reach this gateway from another device is a plain-http LAN address
// (`http://192.168.1.5:10000`) — which is not one. So the common real-world case is exactly the one
// that silently offered nothing: no install affordance, no offline shell, no reason given.
//
// 🪤 A STATUS THAT IS ONLY A COLOUR FAILS 1.4.1. The row says "Available" / "Unavailable" in words and
// carries a shield icon; the tone is confirmation, never the message.
//
// 🪤 AND IT IS NOT A NEW SURFACE. `CompanionPanel` is the MOBILE-COMPANION settings panel — LAN
// discovery and instance name live there — and installing the PWA is how the dashboard gets onto a
// phone. Inventing an "Offline" panel for one row would have been the speculative move.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

// The panel reads its config through `api.personalclawConfig()`; stub it so the row renders.
// `companionDiscovery` (CA-5) is the live advertiser read the same panel makes — stubbed to the
// default "off" answer, which is the state this file's install/offline rows are asserted against.
vi.mock('../../lib/api', () => ({
  api: {
    personalclawConfig: () => Promise.resolve({ companion: { discovery_enabled: false, instance_name: '' } }),
    patchConfig: () => Promise.resolve({}),
    companionDiscovery: () => Promise.resolve({
      advertising: false, reason: 'disabled', detail: 'LAN discovery is off.',
      service_type: '_personalclaw._tcp.local.', instance_name: '', port: 0, addresses: [], txt: {},
    }),
  },
}))

const setSecure = (secure: boolean, hasSW: boolean) => {
  Object.defineProperty(window, 'isSecureContext', { value: secure, configurable: true })
  if (hasSW) {
    Object.defineProperty(navigator, 'serviceWorker', { value: {}, configurable: true })
  } else if ('serviceWorker' in navigator) {
    delete (navigator as unknown as Record<string, unknown>).serviceWorker
  }
}

describe('the Companion panel reports install & offline availability', () => {
  beforeEach(() => { vi.restoreAllMocks() })
  afterEach(() => { setSecure(true, true) })

  it('says Available, in words, on a secure context that supports workers', async () => {
    setSecure(true, true)
    render(<CompanionPanel />)
    expect(await screen.findByText('Available')).toBeTruthy()
    expect(screen.getByText(/app shell is cached/)).toBeTruthy()
  })

  it('says Unavailable AND gives the reason when the page is not a secure context', async () => {
    setSecure(false, true)
    render(<CompanionPanel />)
    expect(await screen.findByText('Unavailable')).toBeTruthy()
    // The whole point: the sentence the function composes must be on screen, not in the console.
    expect(screen.getByText(/not a secure context/)).toBeTruthy()
    expect(screen.getByText(/localhost or an https tunnel/)).toBeTruthy()
  })

  it('gives the no-support reason when the browser has no service worker at all', async () => {
    setSecure(true, false)
    render(<CompanionPanel />)
    expect(await screen.findByText('Unavailable')).toBeTruthy()
    expect(screen.getByText(/no service-worker support/)).toBeTruthy()
  })

  it('states the status in words, so it is not carried by colour alone (1.4.1)', () => {
    const code = read('pages/settings/CompanionPanel.tsx')
    // Both branches must render a WORD; the tone class is confirmation only.
    expect(code).toMatch(/\{swBlocked \? 'Unavailable' : 'Available'\}/)
    expect(code, 'and an icon per state').toMatch(/ShieldAlert|ShieldCheck/)
  })

  it('consumes the shared helper rather than re-deriving the rule', () => {
    // THE POINT OF THE CHANGE. If a later edit inlines its own `isSecureContext` check, the reason
    // string drifts from the one `registerServiceWorker` logs and the two can disagree.
    const code = read('pages/settings/CompanionPanel.tsx')
    expect(code).toMatch(/import \{ serviceWorkerBlockedReason \} from '\.\.\/\.\.\/app\/registerServiceWorker'/)
    expect(code).toMatch(/const swBlocked = serviceWorkerBlockedReason\(\)/)
    expect(code, 'no hand-rolled copy of the rule').not.toMatch(/isSecureContext/)
  })

  it('the helper is no longer console-only — it has a production reader', () => {
    // The vacuity floor: this rail is meaningless if the import disappears, and the censused fact
    // that made this cycle worth doing was "zero production importers".
    const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
      })
    const readers = walk(SRC)
      .filter((abs) => !abs.endsWith(join('app', 'registerServiceWorker.ts')))
      .filter((abs) => /serviceWorkerBlockedReason/.test(readFileSync(abs, 'utf8')))
      .map((abs) => abs.slice(SRC.length + 1))
    expect(readers).toEqual(['pages/settings/CompanionPanel.tsx'])
  })
})
