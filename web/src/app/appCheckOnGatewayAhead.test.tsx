import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'
import type { AppUpdateState, DesktopBridge } from '../lib/desktopBridge'

// ── An older desktop app on a newer gateway checks for its own update at once ───────────────────
//
// 🔴 The app checks at once when it finds its gateway on a newer version (`noteGateway`), but an app
// from before that rule (0.3.7, whose update state has no `ready`) waited for its daily check on a
// gateway already ahead of it: the owner updated the gateway from a browser tab, and the app said
// it was up to date until the next day. The page now asks that app to check, once, while automatic
// checks are on.

vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  return { ...real, api: { ...real.api, updateCheck: () => new Promise(() => {}) } }
})

const { AppCheckOnGatewayAhead, OLD_SHELL_UNTIL, resetAppCheckOnGatewayAhead } = await import('./AppCheckOnGatewayAhead')
const { resetDataStore, writeQuery } = await import('../lib/data/store')
const { compareVersions } = await import('../lib/versions')

function stands(over: Partial<AppUpdateState> = {}): AppUpdateState {
  return {
    current: '0.3.7', how: 'mac', phase: 'current', available: null, checkedAt: 1, progress: null,
    sentence: '', last: null, releasesPage: 'https://example.test/releases', restartsGateway: false, ...over,
  }
}

function fakeApp(state: AppUpdateState) {
  const asked: string[] = []
  const listeners = new Set<(s: AppUpdateState) => void>()
  const appUpdate = {
    status: async () => state,
    check: async () => { asked.push('check'); return state },
    download: async () => ({ ok: true }),
    finish: async () => ({ ok: true }),
    on: (cb: (s: AppUpdateState) => void) => { listeners.add(cb); return () => listeners.delete(cb) },
  }
  window.pclawDesktop = { appUpdate } as unknown as DesktopBridge
  return { asked, push: (s: AppUpdateState) => act(() => { for (const cb of listeners) cb(s) }) }
}

function gatewayOn(current: string, over: Record<string, unknown> = {}) {
  writeQuery('settings:update-check', { available: false, changes: '', checked: true, auto: 'off', current, check_enabled: true, ...over }, true)
}

const settle = () => act(async () => { for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0)) })

beforeEach(() => { resetDataStore(); sessionStorage.clear(); resetAppCheckOnGatewayAhead() })
afterEach(() => { cleanup(); delete (window as { pclawDesktop?: unknown }).pclawDesktop })

describe('an older desktop app on a newer gateway', () => {
  it('🔴 is asked to check at once, and once', async () => {
    gatewayOn('0.3.9')
    const app = fakeApp(stands())
    render(<AppCheckOnGatewayAhead />)
    await settle()
    expect(app.asked).toEqual(['check'])
    app.push(stands({ checkedAt: 2 }))
    await settle()
    expect(app.asked, 'asked again for the same gateway').toEqual(['check'])
  })

  it('is not asked on a gateway on its version or older', async () => {
    for (const current of ['0.3.7', '0.3.6']) {
      resetDataStore(); resetAppCheckOnGatewayAhead()
      gatewayOn(current)
      const app = fakeApp(stands())
      render(<AppCheckOnGatewayAhead />)
      await settle()
      expect(app.asked, current).toEqual([])
      cleanup()
    }
  })

  it('is not asked with automatic update checks off', async () => {
    gatewayOn('0.3.9', { check_enabled: false })
    const app = fakeApp(stands())
    render(<AppCheckOnGatewayAhead />)
    await settle()
    expect(app.asked).toEqual([])
  })

  it('is not asked while an update of it is under way', async () => {
    gatewayOn('0.3.9')
    const app = fakeApp(stands({ phase: 'downloading', available: { version: '0.3.9', installable: true } }))
    render(<AppCheckOnGatewayAhead />)
    await settle()
    expect(app.asked).toEqual([])
  })

  it('leaves an app that checks by itself to its own rule', async () => {
    gatewayOn('0.3.9')
    const app = fakeApp(stands({ current: '0.3.8', ready: null }))
    render(<AppCheckOnGatewayAhead />)
    await settle()
    expect(app.asked).toEqual([])
  })

  it('goes once the gateway no longer serves windows of the app it is for', () => {
    // The gateway refuses windows of a desktop app older than this floor (`pair_refusals`). While it
    // still serves 0.3.7's, this stays; once the floor passes it, nothing needs it: delete it then.
    const py = readFileSync(resolve(__dirname, '../../../src/personalclaw/dashboard/pair_refusals.py'), 'utf8')
    const floor = /^MIN_DESKTOP_VERSION = "([^"]+)"$/m.exec(py)?.[1]
    expect(floor, 'the window floor is read').toBeTruthy()
    expect(compareVersions(floor, OLD_SHELL_UNTIL), `the floor ${floor} passed ${OLD_SHELL_UNTIL}: delete AppCheckOnGatewayAhead`).not.toBe(1)
  })
})
