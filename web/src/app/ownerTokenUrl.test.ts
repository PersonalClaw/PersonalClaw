import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

// ── The owner-token link's browser half, executed for real ──────────────────────────────────
//
// `src/personalclaw/dashboard/owner_token_url.js` is the ONE script the gateway inlines into the
// SPA document a `?token=` link opens, the paste-token Connect gate, and the sign-in page. It is
// package data rather than a module of this bundle — the gate renders before any authenticated
// asset can load — so this suite reads the shipped file and evaluates it in jsdom, the same bytes
// a browser runs.
//
// Measured before it existed (day-56b `s26`): opening `#/chat/<key>` without a session showed
// the Connect gate; pasting the token landed on `/?token=<tok>#/dashboard` — the chat was gone
// (the gate built `origin + '?token='`) and the owner token sat in the address bar and the
// history entry for the rest of the visit.

interface OwnerToken {
  route(hash: string): string
  scrub(): boolean
  connectUrl(raw: string): string
  home(): string
}

declare global {
  interface Window { PersonalClawOwnerToken: OwnerToken }
}

const SOURCE = readFileSync(resolve(process.cwd(), '../src/personalclaw/dashboard/owner_token_url.js'), 'utf8')
const T = () => window.PersonalClawOwnerToken

beforeAll(() => { window.eval(SOURCE) })
beforeEach(() => {
  history.replaceState(null, '', '/')
  vi.restoreAllMocks()
})

describe('scrub — the token leaves the address bar and the history entry', () => {
  it('removes the token and keeps the path, the other query and the route', () => {
    history.replaceState({ entry: 'link' }, '', '/?token=OWNER-SECRET&keep=1#/chat/chat-9-1790357831')
    const replace = vi.spyOn(history, 'replaceState')

    expect(T().scrub()).toBe(true)

    expect(location.href).not.toContain('OWNER-SECRET')
    expect(location.search).toBe('?keep=1')
    expect(location.hash).toBe('#/chat/chat-9-1790357831')
    // REPLACES the entry (Back cannot return to the token URL) and keeps its state object.
    expect(replace).toHaveBeenCalledTimes(1)
    expect(history.state).toEqual({ entry: 'link' })
  })

  it('removes a repeated token parameter too', () => {
    history.replaceState(null, '', '/?token=a&token=b#/dashboard')
    T().scrub()
    expect(location.href).not.toContain('token=')
  })

  it('leaves a clean URL alone', () => {
    history.replaceState(null, '', '/?keep=1#/dashboard')
    const replace = vi.spyOn(history, 'replaceState')
    expect(T().scrub()).toBe(false)
    expect(replace).not.toHaveBeenCalled()
  })
})

describe('connectUrl — the Connect gate keeps the deep link, and only a same-origin one', () => {
  it('lands on the route the gate was opened at, with the pasted token', () => {
    history.replaceState(null, '', '/#/chat/chat-9-1790357831')
    const target = new URL(T().connectUrl('http://127.0.0.1:10000/?token=FRESH'))
    expect(target.origin).toBe(location.origin)
    expect(target.pathname).toBe('/')
    expect(target.searchParams.get('token')).toBe('FRESH')
    expect(target.hash).toBe('#/chat/chat-9-1790357831')
  })

  it('keeps a route that carries its own view query', () => {
    history.replaceState(null, '', '/#/chat/chat-9-1790357831?find=hash%20map')
    expect(new URL(T().connectUrl('RAW-TOKEN')).hash).toBe('#/chat/chat-9-1790357831?find=hash%20map')
  })

  it('takes ONLY the token from a pasted URL — never its origin, path or route', () => {
    history.replaceState(null, '', '/#/settings')
    const target = new URL(T().connectUrl('https://evil.example/elsewhere?token=FRESH#/wrong'))
    expect(target.origin).toBe(location.origin)
    expect(target.pathname).toBe('/')
    expect(target.hash).toBe('#/settings')
  })

  it('accepts a raw token, and refuses a URL with none in it', () => {
    expect(new URL(T().connectUrl('  RAW-TOKEN  ')).searchParams.get('token')).toBe('RAW-TOKEN')
    expect(T().connectUrl('https://example.invalid/no-credential-here')).toBe('')
    expect(T().connectUrl('   ')).toBe('')
  })

  it('drops a fragment that is not a dashboard route', () => {
    for (const hash of ['#//evil.example/x', '#javascript:alert(1)', '#\\\\evil', '#settings', '#/' + 'x'.repeat(3000)]) {
      history.replaceState(null, '', '/' + hash)
      const target = new URL(T().connectUrl('RAW-TOKEN'))
      expect(target.origin, hash).toBe(location.origin)
      expect(target.hash, hash).toBe('')
    }
  })
})

describe('home — where a successful sign-in lands', () => {
  it('is the root plus the route the user opened, not a bare "/"', () => {
    history.replaceState(null, '', '/login#/projects/p-1')
    expect(T().home()).toBe('/#/projects/p-1')
    history.replaceState(null, '', '/login')
    expect(T().home()).toBe('/')
  })
})
