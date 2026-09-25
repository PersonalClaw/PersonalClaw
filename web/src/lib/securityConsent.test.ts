/**
 * The owner's consent for a loosening security write is asked ONCE, in the gateway's words, for
 * every surface — including one that never heard the field was sensitive.
 *
 * The gateway answers a write that loosens a field on its security list with
 * `400 confirmation_required` and `{field, consent}` in `error.detail` (`config/edit_spec.py`).
 * `withSecurityConsent` turns that into one dialog and one resend carrying `confirm: true`. The
 * last block drives the real `api.patchConfig` through a stubbed `fetch`, so what is asserted is
 * the wire: the first body carries no consent, and only an accepted dialog produces a second one
 * that does.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConsentDeclined, consentAsked, withSecurityConsent } from './securityConsent'
import { api, ApiError } from './api'

const confirmSpy = vi.hoisted(() => vi.fn(async (_opts: unknown) => true))
vi.mock('../ui/dialog', () => ({ confirm: (opts: unknown) => confirmSpy(opts) }))

const CONSENT = 'Turning YOLO on skips every tool-approval confirmation, for every session, until it is turned off.'

const asked = () =>
  new ApiError('send {"confirm": true} to confirm', 400, 'confirmation_required', {
    field: 'agent.yolo',
    consent: CONSENT,
  })

afterEach(() => {
  confirmSpy.mockReset()
  confirmSpy.mockImplementation(async () => true)
  vi.unstubAllGlobals()
})

describe('consentAsked', () => {
  it('reads the gateway question, and nothing else', () => {
    expect(consentAsked(asked())).toEqual({ field: 'agent.yolo', consent: CONSENT })
    expect(consentAsked(new ApiError('nope', 400, 'invalid_request'))).toBeNull()
    expect(consentAsked(new ApiError('no detail', 400, 'confirmation_required'))).toBeNull()
    expect(consentAsked(new Error('network'))).toBeNull()
  })
})

describe('withSecurityConsent', () => {
  it('a write the gateway accepts is sent once and asks nothing', async () => {
    const send = vi.fn(async (_c: boolean) => 'ok')
    await expect(withSecurityConsent(send)).resolves.toBe('ok')
    expect(send.mock.calls).toEqual([[false]])
    expect(confirmSpy).not.toHaveBeenCalled()
  })

  it('asks in the gateway’s words, then resends WITH consent', async () => {
    const send = vi.fn(async (c: boolean) => {
      if (!c) throw asked()
      return 'written'
    })
    await expect(withSecurityConsent(send)).resolves.toBe('written')
    expect(send.mock.calls).toEqual([[false], [true]])
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    const opts = confirmSpy.mock.calls[0][0] as { body: string; danger: boolean }
    expect(opts.body).toBe(CONSENT)
    expect(opts.danger).toBe(true)
  })

  it('a decline sends nothing more, and says nothing changed', async () => {
    confirmSpy.mockImplementation(async () => false)
    const send = vi.fn(async (_c: boolean) => {
      throw asked()
    })
    const err = await withSecurityConsent(send).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ConsentDeclined)
    expect((err as InstanceType<typeof ConsentDeclined>).field).toBe('agent.yolo')
    expect((err as Error).message).toMatch(/not changed/i)
    expect(send).toHaveBeenCalledTimes(1)
  })

  it('a caller that already asked sends consent first and is never asked again', async () => {
    const send = vi.fn(async (_c: boolean) => {
      throw asked()
    })
    await expect(withSecurityConsent(send, true)).rejects.toBeInstanceOf(ApiError)
    expect(send.mock.calls).toEqual([[true]])
    expect(confirmSpy).not.toHaveBeenCalled()
  })

  it('any other refusal passes through untouched', async () => {
    const refusal = new ApiError('must be a boolean', 400, 'invalid_request')
    const send = vi.fn(async (_c: boolean) => {
      throw refusal
    })
    await expect(withSecurityConsent(send)).rejects.toBe(refusal)
    expect(confirmSpy).not.toHaveBeenCalled()
  })
})

describe('api.patchConfig on the wire', () => {
  const reply = (status: number, o: unknown) =>
    new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } })

  /** A gateway that refuses the unconsented loosening write, as the real PATCH does. */
  function gateway() {
    const bodies: Record<string, unknown>[] = []
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>
      bodies.push(body)
      if (body.confirm !== true) {
        return reply(400, {
          error: {
            code: 'confirmation_required',
            message: 'send {"confirm": true} to confirm',
            detail: { field: body.path, consent: CONSENT },
          },
        })
      }
      return reply(200, { agent: { yolo: true } })
    }))
    return bodies
  }

  it('asks the owner, and only the accepted resend carries confirm: true', async () => {
    const bodies = gateway()
    await expect(api.patchConfig('agent.yolo', true)).resolves.toEqual({ agent: { yolo: true } })
    expect(bodies).toEqual([
      { path: 'agent.yolo', value: true },
      { path: 'agent.yolo', value: true, confirm: true },
    ])
    expect(confirmSpy).toHaveBeenCalledTimes(1)
  })

  it('a declined dialog sends nothing after the refusal', async () => {
    confirmSpy.mockImplementation(async () => false)
    const bodies = gateway()
    await expect(api.patchConfig('agent.yolo', true)).rejects.toBeInstanceOf(ConsentDeclined)
    expect(bodies).toEqual([{ path: 'agent.yolo', value: true }])
  })

  it('a surface that asked its own question consents on the first request', async () => {
    const bodies = gateway()
    await api.patchConfig('agent.yolo', true, true)
    expect(bodies).toEqual([{ path: 'agent.yolo', value: true, confirm: true }])
    expect(confirmSpy).not.toHaveBeenCalled()
  })

  it('the dedicated security writers take the same path', async () => {
    const bodies = gateway()
    await api.setSecurityEgress({ allow_hosts: [], deny_hosts: [], allow_private: true }, true)
    await api.setMcpElicitationServers(['srv'])
    expect(bodies[0]).toMatchObject({ path: 'security.egress', confirm: true })
    expect(bodies.slice(1)).toEqual([
      { path: 'security.mcp_elicitation_servers', value: ['srv'] },
      { path: 'security.mcp_elicitation_servers', value: ['srv'], confirm: true },
    ])
  })
})
