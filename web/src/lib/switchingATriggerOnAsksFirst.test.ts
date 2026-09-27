/**
 * Switching a trigger on can grant what its action runs, so the gateway may ask first — and the
 * owner is asked in its words before the switch is resent with consent.
 *
 * A trigger an upgrade brought over from an older version has been allowed nothing
 * (`triggers/legacy_import.py`): `POST /api/triggers/{id}/toggle` answers
 * `400 confirmation_required` with the sentence to show, until the request carries
 * `confirm: true`. Before this, both switches posted `{enabled}` and nothing else, so the page had
 * no way to carry the owner's answer. Driven through a stubbed `fetch`, so what is asserted is the
 * wire: the first body carries no consent, and only an accepted dialog sends a second one that does.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { ConsentDeclined } from './securityConsent'

const confirmSpy = vi.hoisted(() => vi.fn(async (_opts: unknown) => true))
vi.mock('../ui/dialog', () => ({ confirm: (opts: unknown) => confirmSpy(opts) }))

const CONSENT =
  '“deploy-hook” was brought over from an older version of PersonalClaw and has not been ' +
  'allowed to run here. Switching it on allows it to use the “Bash Command” action when it fires.'

const reply = (status: number, o: unknown) =>
  new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } })

/** A gateway that refuses an unconsented switch-on, as the toggle route does for such a row. */
function gateway() {
  const sent: Array<{ url: string; body: Record<string, unknown> }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body)) as Record<string, unknown>
    sent.push({ url, body })
    if (body.enabled === true && body.confirm !== true) {
      return reply(400, {
        error: {
          code: 'confirmation_required',
          message: 'send {"confirm": true} to confirm',
          detail: { field: 'triggers.store:event:deploy-hook.capabilities', consent: CONSENT, title: 'Allow this trigger to run?' },
        },
      })
    }
    return reply(200, { ok: true })
  }))
  return sent
}

afterEach(() => {
  confirmSpy.mockReset()
  confirmSpy.mockImplementation(async () => true)
  vi.unstubAllGlobals()
})

describe('switching a store trigger on', () => {
  it('asks the owner in the gateway’s words, and only the accepted resend carries confirm', async () => {
    const sent = gateway()
    await api.toggleStoreTrigger('event:deploy-hook', true)

    expect(sent.map((s) => s.body)).toEqual([{ enabled: true }, { enabled: true, confirm: true }])
    expect(sent[0].url).toContain('/api/triggers/store:event%3Adeploy-hook/toggle')
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    const opts = confirmSpy.mock.calls[0][0] as { title: string; body: string }
    expect(opts.body).toBe(CONSENT)
    expect(opts.title).toBe('Allow this trigger to run?')
  })

  it('a declined dialog sends nothing after the refusal, and the switch stays off', async () => {
    confirmSpy.mockImplementation(async () => false)
    const sent = gateway()
    await expect(api.toggleStoreTrigger('event:deploy-hook', true)).rejects.toBeInstanceOf(ConsentDeclined)
    expect(sent.map((s) => s.body)).toEqual([{ enabled: true }])
  })

  it('switching one off never asks', async () => {
    const sent = gateway()
    await api.toggleStoreTrigger('event:deploy-hook', false)
    expect(sent.map((s) => s.body)).toEqual([{ enabled: false }])
    expect(confirmSpy).not.toHaveBeenCalled()
  })
})

describe('switching a schedule on', () => {
  it('asks the same way — an imported cron waits for the same decision', async () => {
    const sent = gateway()
    await api.enableSchedule('nightly', true)
    expect(sent.map((s) => s.body)).toEqual([{ enabled: true }, { enabled: true, confirm: true }])
    expect(sent[0].url).toContain('/api/triggers/schedule:nightly/toggle')
  })
})
