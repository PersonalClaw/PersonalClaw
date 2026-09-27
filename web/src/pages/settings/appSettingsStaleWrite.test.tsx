import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import type { AppSummary } from '../../lib/api'

// ── An app's settings are saved over the copy its form was read at ──────────────────────────────
//
// `PUT /api/apps/{name}/config` replaces the app's whole settings file — and deletes the
// credentials the new one stops referencing — and the same file is written by Settings →
// Providers, by another tab, and by the app itself. The form used to PUT its own copy back over
// whatever had been saved since. The save now names the revision the form read; a stale one is
// refused, the form keeps what the user typed, and the edit can be re-applied field by field on top
// of what is stored. Driven through Settings → Apps, one of the hook's two consumers.

type Stored = { config: Record<string, unknown>; revision: string }

/** The gateway's copy of the file — what a save is checked against. */
let stored: Stored = { config: {}, revision: '' }
let written = 0
const saveAppConfig = vi.fn((name: string, config: Record<string, unknown>, base: string) => {
  if (base !== stored.revision) {
    return Promise.reject(new ApiError(`The app '${name}''s settings changed.`, 409, 'stale_write'))
  }
  stored = { config, revision: `rev-w${++written}` }
  return Promise.resolve({ ok: true, config, revision: stored.revision })
})

const SCHEMA = {
  properties: {
    room: { type: 'string', 'x-meta': { label: 'Room' } },
    greeting: { type: 'string', 'x-meta': { label: 'Greeting' } },
  },
}
const APP = {
  name: 'slack-channel', displayName: 'Slack', version: '1.0.0', description: '', enabled: true, icon: 'plug',
  hasUI: false, isProvider: false, hasConfig: true,
} as AppSummary

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<typeof import('../../lib/api')>()),
  api: {
    apps: () => Promise.resolve([APP]),
    personalclawConfig: () => Promise.resolve({ apps: {} }),
    appConfig: (name: string) => Promise.resolve({ name, schema: SCHEMA, config: stored.config, _secret_set: [], revision: stored.revision }),
    saveAppConfig: (n: string, c: Record<string, unknown>, b: string) => saveAppConfig(n, c, b),
  },
}))

import { AppsPanel } from './AppsPanel'
import { ApiError } from '../../lib/api'
import { resetDataStore } from '../../lib/data'

beforeEach(() => {
  resetDataStore()
  sessionStorage.clear()
  written = 0
  saveAppConfig.mockClear()
  stored = { config: { room: 'general', greeting: 'hi' }, revision: 'rev-1' }
})

const field = (key: string) => document.getElementById(`app-cfg-slack-channel-${key}`) as HTMLInputElement

/** Open Settings → Apps on the stored copy, change the greeting, let `meanwhile` happen, Save. */
async function editGreeting(meanwhile: () => void = () => {}) {
  render(<AppsPanel />)
  await waitFor(() => expect(field('greeting')).toBeTruthy())
  fireEvent.change(field('greeting'), { target: { value: 'hello' } })
  meanwhile()
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
}

describe("an app's settings are saved over the copy the form read", () => {
  it('a save names the revision the form read', async () => {
    await editGreeting()
    await waitFor(() => expect(saveAppConfig).toHaveBeenCalledWith('slack-channel', { room: 'general', greeting: 'hello' }, 'rev-1'))
    expect(screen.queryByText(/changed elsewhere/)).toBeNull()
  })

  it('settings saved elsewhere since are not overwritten: the edit is kept, then re-applied on top', async () => {
    // Another tab moves the app to another room while this form is open.
    await editGreeting(() => { stored = { config: { room: 'random', greeting: 'hi' }, revision: 'rev-2' } })

    const said = await screen.findByText(/changed elsewhere/)
    expect(said.closest('[role="alert"]')).not.toBeNull()
    // What the user typed is still in the form, and the file is as the other tab left it.
    expect(field('greeting').value).toBe('hello')
    expect(stored).toEqual({ config: { room: 'random', greeting: 'hi' }, revision: 'rev-2' })

    const reapply = await screen.findByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.getAttribute('aria-disabled')).not.toBe('true'))
    fireEvent.click(reapply)
    // The greeting on top of what is stored now, over ITS revision: the other tab's room survives.
    await waitFor(() => expect(saveAppConfig).toHaveBeenLastCalledWith('slack-channel', { room: 'random', greeting: 'hello' }, 'rev-2'))
    await waitFor(() => expect(screen.queryByText(/changed elsewhere/)).toBeNull())
    expect(stored.config).toEqual({ room: 'random', greeting: 'hello' })
  })
})
