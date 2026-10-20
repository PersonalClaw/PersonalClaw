/**
 * The owner's consent for a loosening security write is asked ONCE, in the gateway's words, for
 * every surface — including one that never heard the field was sensitive.
 *
 * The gateway answers a write that loosens a field on its security list with
 * `400 confirmation_required` and `{field, consent, title}` in `error.detail`
 * (`config/edit_spec.py`), and so does a write whose action needs a grant (`triggers/grants.py`).
 * `withSecurityConsent` turns that into one dialog, headed with the gateway's own title, and one
 * resend carrying `confirm: true`. The
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
const LOOSEN = 'Loosen a security setting?'

const asked = (title = LOOSEN) =>
  new ApiError('send {"confirm": true} to confirm', 400, 'confirmation_required', {
    field: 'agent.yolo',
    consent: CONSENT,
    title,
  })

afterEach(() => {
  confirmSpy.mockReset()
  confirmSpy.mockImplementation(async () => true)
  vi.unstubAllGlobals()
})

describe('consentAsked', () => {
  it('reads the gateway question, and nothing else', () => {
    expect(consentAsked(asked())).toEqual({ field: 'agent.yolo', consent: CONSENT, title: LOOSEN })
    expect(consentAsked(new ApiError('nope', 400, 'invalid_request'))).toBeNull()
    expect(consentAsked(new ApiError('no detail', 400, 'confirmation_required'))).toBeNull()
    // The heading is part of the question: without one there is no dialog to show.
    expect(consentAsked(new ApiError('untitled', 400, 'confirmation_required', {
      field: 'agent.yolo', consent: CONSENT,
    }))).toBeNull()
    expect(consentAsked(new Error('network'))).toBeNull()
  })
})

describe('a loosening is asked with what it changes, from and to', () => {
  // The gateway's sentence is written once per field, so on its own it asked the same thing
  // whatever was typed: a daily cap of $33.50 typed as $10,033.50 read exactly like a raise to
  // $100. A loosening's question now carries `change` and, for a raise of ten times or more,
  // `caution` (`config/edit_spec.LooseningAsk`), and the dialog says both under the sentence.
  const CAP = 'The agent may spend more money per day — 0 removes the limit.'
  const CHANGE = '$33.50 → $10,033.50'
  const CAUTION = 'That is about 300 times the current limit, so check the number before you allow it.'
  const loosening = (extra: Record<string, unknown>) =>
    new ApiError('send {"confirm": true} to confirm', 400, 'confirmation_required', {
      field: 'guardrails.budgets.max_dollars_per_day', consent: CAP, title: LOOSEN, ...extra,
    })

  it('reads the change and the caution the gateway sends', () => {
    expect(consentAsked(loosening({ change: CHANGE, caution: CAUTION }))).toEqual({
      field: 'guardrails.budgets.max_dollars_per_day', consent: CAP, title: LOOSEN, change: CHANGE, caution: CAUTION,
    })
    // A question that is not a loosening carries neither, and reads as it always did.
    expect(consentAsked(asked())).toEqual({ field: 'agent.yolo', consent: CONSENT, title: LOOSEN })
  })

  it('the dialog says the sentence, then the change, then the second look — one paragraph each', async () => {
    const send = vi.fn(async (c: boolean) => {
      if (!c) throw loosening({ change: CHANGE, caution: CAUTION })
      return 'written'
    })
    await withSecurityConsent(send)
    const opts = confirmSpy.mock.calls[0][0] as { title: string; body: string }
    expect(opts.title).toBe(LOOSEN)
    expect(opts.body).toBe(`${CAP}\n\n${CHANGE}\n\n${CAUTION}`)
  })

  it('a raise under ten times has no second look, only the change', async () => {
    const send = vi.fn(async (c: boolean) => {
      if (!c) throw loosening({ change: '$33.50 → $100.00' })
      return 'written'
    })
    await withSecurityConsent(send)
    expect((confirmSpy.mock.calls[0][0] as { body: string }).body).toBe(`${CAP}\n\n$33.50 → $100.00`)
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
    const opts = confirmSpy.mock.calls[0][0] as { title: string; body: string; danger: boolean }
    expect(opts.title).toBe(LOOSEN)
    expect(opts.body).toBe(CONSENT)
    expect(opts.danger).toBe(true)
  })

  it('the dialog is headed with the question the gateway asked, not one fixed heading', async () => {
    // Measured before this: every question read "Loosen a security setting?", a plain grant for
    // what a trigger runs included — a heading that was untrue of the question under it.
    const send = vi.fn(async (c: boolean) => {
      if (!c) throw asked('Allow what this trigger runs?')
      return 'created'
    })
    await expect(withSecurityConsent(send)).resolves.toBe('created')
    expect((confirmSpy.mock.calls[0][0] as { title: string }).title).toBe('Allow what this trigger runs?')
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
            detail: { field: body.path, consent: CONSENT, title: LOOSEN },
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
    await api.setSecurityEgress({ allow_hosts: [], deny_hosts: [], allow_private: true }, 'r1', true)
    await api.grantMcpElicitation('srv')
    await api.removeDeniedCommand('rm -rf')
    expect(bodies[0]).toMatchObject({ path: 'security.egress', confirm: true })
    // One name in or out — never the page's copy of the list — and still asked about.
    expect(bodies.slice(1)).toEqual([
      { path: 'security.mcp_elicitation_servers', add: 'srv' },
      { path: 'security.mcp_elicitation_servers', add: 'srv', confirm: true },
      { path: 'security.denied_commands', remove: 'rm -rf' },
      { path: 'security.denied_commands', remove: 'rm -rf', confirm: true },
    ])
  })
})

describe('an automation whose agent approves itself asks the same way', () => {
  // `POST/PUT /api/triggers`, `POST /api/workflows`, the run-override PUT and the agent sync
  // answer a loosening write `400 confirmation_required` (`automation_posture.py`,
  // `supervisor_policy.POLICY_OVERRIDE_SECURITY`, `agents._sync_consent`). Each writer here must
  // turn that into the dialog and ONE resend with `confirm: true`, never a silent failure.
  const reply = (status: number, o: unknown) =>
    new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } })

  function gateway(ok: unknown) {
    const sent: Array<{ url: string; method: string; body: Record<string, unknown>; ifMatch?: string }> = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      const body = (init.body ? JSON.parse(String(init.body)) : {}) as Record<string, unknown>
      sent.push({ url, method: String(init.method), body, ifMatch: (init.headers as Record<string, string> | undefined)?.['If-Match'] })
      if (body.confirm !== true) {
        return reply(400, {
          error: {
            code: 'confirmation_required',
            message: 'send {"confirm": true} to confirm',
            detail: { field: 'triggers.t.action.approval_mode', consent: CONSENT, title: LOOSEN },
          },
        })
      }
      return reply(200, ok)
    }))
    return sent
  }

  const trigger = { id: 'schedule:t', raw_id: 't', kind: 'schedule', name: 't', action: { provider: 'invoke-agent', config: {} } }
  const writers: Array<[string, () => Promise<unknown>, unknown]> = [
    ['createSchedule', () => api.createSchedule({ name: 't', every: 300, approval_mode: 'auto' }), { ok: true, trigger }],
    ['updateSchedule', () => api.updateSchedule('t', { approval_mode: 'auto' }, 's1'), { ok: true, trigger }],
    ['createEvent', () => api.createEvent({ pattern: 'AppEvent', action: { provider: 'invoke-agent', config: { approval_mode: 'auto' } } }), { ok: true, trigger: { ...trigger, kind: 'store' } }],
    ['createHook', () => api.createHook({ name: 'h', event: 'stop', provider: 'invoke-agent', provider_config: { approval_mode: 'auto' } }), { ok: true, trigger: { ...trigger, kind: 'lifecycle' } }],
    ['updateHook', () => api.updateHook('h', { provider: 'invoke-agent', provider_config: { approval_mode: 'auto' } }, 'h1'), { ok: true, trigger: { ...trigger, kind: 'lifecycle' } }],
    ['saveWorkflowDef', () => api.saveWorkflowDef({ name: 'w', root: { kind: 'stage', id: 's', config: { approval_mode: 'auto' } }, save: true }), { saved: true, valid: true, issues: [] }],
    ['setWorkflowRunPolicyOverrides', () => api.setWorkflowRunPolicyOverrides('r1', { max_cycles: 9 }, 'v1'), { run_id: 'r1', status: 'running', policy_overrides: { max_cycles: 9 }, revisions: { policy_overrides: 'v2' } }],
    ['editWorkflowRun', () => api.editWorkflowRun('r1', { ops: [{ op: 'update_node', node_id: 'fix', fields: { prompt: 'Fix the lint.' } }] }), { ok: true, queued: true, preview: { rerun: [], stale: [], skipped: [] }, issues: [] }],
    ['syncAgents', () => api.syncAgents(), { ok: true, synced: ['helper'], skipped: [], unreadable: [], scanned: 1, message: '' }],
  ]

  it.each(writers)('%s asks once, then resends the same write with confirm: true', async (_name, write, ok) => {
    const sent = gateway(ok)
    await write()
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(sent).toHaveLength(2)
    expect(sent[0].body.confirm).toBeUndefined()
    expect(sent[1].body.confirm).toBe(true)
    // The resend is the SAME request, only consented: same route, same verb, same payload.
    expect(sent[1].url).toBe(sent[0].url)
    expect(sent[1].method).toBe(sent[0].method)
    const { confirm: _c, ...resent } = sent[1].body
    expect(resent).toEqual(sent[0].body)
    // …and over the same base: a whole-document write's resend names the revision the first did.
    expect(sent[1].ifMatch).toBe(sent[0].ifMatch)
  })

  it('the run-override resend carries the base revision the first write named', async () => {
    const sent = gateway({ run_id: 'r1', status: 'draft', policy_overrides: { max_cycles: 0 }, revisions: { policy_overrides: 'v2' } })
    await api.setWorkflowRunPolicyOverrides('r1', { max_cycles: 0 }, 'v1')
    expect(sent.map((s) => s.ifMatch)).toEqual(['"v1"', '"v1"'])
  })

  it('an automation edit and its consented resend both name the copy the form was built from', async () => {
    // The edit forms save the WHOLE trigger, so the base is not optional — and a resend that dropped
    // it would be a 428 behind a dialog the user already answered.
    const schedule = gateway({ ok: true, trigger })
    await api.updateSchedule('t', { approval_mode: 'auto' }, 's1')
    expect(schedule.map((s) => s.ifMatch)).toEqual(['"s1"', '"s1"'])
    const hook = gateway({ ok: true, trigger: { ...trigger, kind: 'lifecycle' } })
    await api.updateHook('h', { provider: 'invoke-agent', provider_config: { approval_mode: 'auto' } }, 'h1')
    expect(hook.map((s) => s.ifMatch)).toEqual(['"h1"', '"h1"'])
  })

  it.each(writers)('%s sends nothing more when the owner declines', async (_name, write, ok) => {
    confirmSpy.mockImplementation(async () => false)
    const sent = gateway(ok)
    await expect(write()).rejects.toBeInstanceOf(ConsentDeclined)
    expect(sent).toHaveLength(1)
  })
})

describe('the consent question is an answer, not a failed request', () => {
  // The gateway answers a write that needs the owner's yes with the question. Sent as a 400, the
  // browser logged every Allow the owner was asked for as a failed request, though nothing had
  // failed. A page that says it asks (`X-PersonalClaw-Consent: ask`, on every write `api.ts`
  // sends) gets the question as a 200 marked `X-PersonalClaw-Consent-Asked`
  // (`dashboard/consent_ask.py`); any other client still gets the 400.
  const QUESTION = 'Allowing “Water the plants” lets the heartbeat run it with your agent’s tools.'
  const TITLE = 'Allow this heartbeat task to run?'

  function gateway() {
    const sent: Array<{ asks: string | undefined; body: Record<string, unknown> }> = []
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
      const headers = init.headers as Record<string, string>
      const body = JSON.parse(String(init.body)) as Record<string, unknown>
      sent.push({ asks: headers['X-PersonalClaw-Consent'], body })
      if (body.confirm !== true) {
        const asks = headers['X-PersonalClaw-Consent'] === 'ask'
        return new Response(JSON.stringify({
          error: {
            code: 'confirmation_required',
            message: 'send {"confirm": true} to confirm',
            detail: { field: 'heartbeat_task', consent: QUESTION, title: TITLE },
          },
        }), {
          status: asks ? 200 : 400,
          headers: {
            'Content-Type': 'application/json',
            ...(asks ? { 'X-PersonalClaw-Consent-Asked': '1' } : {}),
          },
        })
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 })
    }))
    return sent
  }

  it('every write says it asks, and the Allow is asked and sent once more', async () => {
    const sent = gateway()
    await expect(api.allowHeartbeatTask('Water the plants')).resolves.toEqual({ ok: true })
    expect(sent.map((s) => s.asks)).toEqual(['ask', 'ask'])
    expect(sent.map((s) => s.body)).toEqual([
      { text: 'Water the plants' },
      { text: 'Water the plants', confirm: true },
    ])
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    const opts = confirmSpy.mock.calls[0][0] as { title: string; body: string }
    expect(opts).toMatchObject({ title: TITLE, body: QUESTION })
  })

  it('a question that came back 200 is never taken for the write having happened', async () => {
    confirmSpy.mockImplementation(async () => false)
    const sent = gateway()
    await expect(api.allowHeartbeatTask('Water the plants')).rejects.toBeInstanceOf(ConsentDeclined)
    expect(sent).toHaveLength(1)
  })
})

describe('an Allow of a workflow is held to the version its question showed', () => {
  // The question an automation's Allow answers names the version of the workflow it runs, and
  // carries what it showed (`shown`, `triggers/grants.py`). The yes sends it back, so a save that
  // lands while the dialog is open is not what the yes allows: the gateway refuses it
  // (`409 stale_write`, nothing changed) with the question as it is now, which says who saved
  // since, and the owner is asked again.
  const SHOWN = { name: 'weekly-report', version: 1, digest: 'a'.repeat(64) }
  const AGAIN = { name: 'weekly-report', version: 2, digest: 'b'.repeat(64) }
  const QUESTION = 'Allowing “Weekly report” lets it use the “Run workflow” action, as it is now, when it runs. It runs “weekly-report” as it is when you allow it (version 1 now).'
  const MOVED = '“weekly-report” changed after you were asked about version 1: v2 by an agent.'
  const AGAIN_QUESTION = `${MOVED} Allowing “Weekly report” lets it use the “Run workflow” action, as it is now, when it runs. It runs “weekly-report” as it is when you allow it (version 2 now, saved by an agent).`
  const TITLE = 'Allow this trigger to run?'

  const asks = (shown: Record<string, unknown>, consent = QUESTION) =>
    new ApiError('send {"confirm": true} to confirm', 400, 'confirmation_required', {
      field: 'triggers.store:manual:weekly-report.capabilities', consent, title: TITLE, shown,
    })
  const asksAgain = (shown: Record<string, unknown>) =>
    new ApiError(`Nothing was changed: ${MOVED} Look at it again before you allow it.`, 409, 'stale_write', {
      field: 'triggers.store:manual:weekly-report.capabilities', consent: AGAIN_QUESTION, title: TITLE, shown,
    })

  it('reads what the question showed, and only an object', () => {
    expect(consentAsked(asks(SHOWN))?.shown).toEqual(SHOWN)
    expect(consentAsked(new ApiError('x', 400, 'confirmation_required', {
      field: 'f', consent: CONSENT, title: LOOSEN, shown: 'version 1',
    }))?.shown).toBeUndefined()
  })

  it('the yes sends back what its question showed', async () => {
    const send = vi.fn(async (c: boolean, _shown?: Record<string, unknown>) => {
      if (!c) throw asks(SHOWN)
      return 'allowed'
    })
    await expect(withSecurityConsent(send)).resolves.toBe('allowed')
    expect(send.mock.calls).toEqual([[false], [true, SHOWN]])
  })

  it('a yes refused because the workflow moved is asked again, in the new words', async () => {
    const send = vi.fn(async (c: boolean, shown?: Record<string, unknown>) => {
      if (!c) throw asks(SHOWN)
      if (shown?.version === 1) throw asksAgain(AGAIN)
      return 'allowed'
    })
    await expect(withSecurityConsent(send)).resolves.toBe('allowed')
    expect(send.mock.calls).toEqual([[false], [true, SHOWN], [true, AGAIN]])
    expect(confirmSpy).toHaveBeenCalledTimes(2)
    const second = confirmSpy.mock.calls[1][0] as { title: string; body: string }
    expect(second.title).toBe(TITLE)
    expect(second.body).toBe(AGAIN_QUESTION)
  })

  it('declining the second question sends nothing more', async () => {
    confirmSpy.mockImplementationOnce(async () => true).mockImplementationOnce(async () => false)
    const send = vi.fn(async (c: boolean, _shown?: Record<string, unknown>) => {
      if (!c) throw asks(SHOWN)
      throw asksAgain(AGAIN)
    })
    await expect(withSecurityConsent(send)).rejects.toBeInstanceOf(ConsentDeclined)
    expect(send).toHaveBeenCalledTimes(2)
  })

  it('a stale write that asks nothing passes through untouched', async () => {
    const stale = new ApiError('The document changed since you read it.', 409, 'stale_write')
    const send = vi.fn(async (c: boolean) => {
      if (!c) throw asks(SHOWN)
      throw stale
    })
    await expect(withSecurityConsent(send)).rejects.toBe(stale)
    expect(confirmSpy).toHaveBeenCalledTimes(1)
  })

  describe('on the wire', () => {
    /** A gateway that asks with `shown`, refuses the first yes as moved (asked again, as the 200
     *  this page is answered a question with), and takes the second. */
    function gateway(ok: unknown) {
      const bodies: Record<string, unknown>[] = []
      vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
        const body = (init.body ? JSON.parse(String(init.body)) : {}) as Record<string, unknown>
        bodies.push(body)
        const asked = (code: string, detail: Record<string, unknown>) => new Response(JSON.stringify({
          error: { code, message: 'asked', detail },
        }), { status: 200, headers: { 'Content-Type': 'application/json', 'X-PersonalClaw-Consent-Asked': '1' } })
        if (body.confirm !== true) {
          return asked('confirmation_required', { field: 'f', consent: QUESTION, title: TITLE, shown: SHOWN })
        }
        if ((body.shown as { version?: number } | undefined)?.version !== 2) {
          return asked('stale_write', { field: 'f', consent: AGAIN_QUESTION, title: TITLE, shown: AGAIN })
        }
        return new Response(JSON.stringify(ok), { status: 200 })
      }))
      return bodies
    }

    const trigger = { id: 'store:manual:weekly-report', raw_id: 'manual:weekly-report', kind: 'store', name: 'Weekly report', action: { provider: 'run-workflow', config: { workflow: 'weekly-report' } } }
    const RUNS = { provider: 'run-workflow', config: { workflow: 'weekly-report' } }
    const writers: Array<[string, () => Promise<unknown>, unknown]> = [
      ['toggleStoreTrigger', () => api.toggleStoreTrigger('manual:weekly-report', true), { ok: true }],
      ['enableSchedule', () => api.enableSchedule('friday-report', true), { ok: true }],
      ['toggleHook', () => api.toggleHook('h', true), { ok: true }],
      ['createSchedule', () => api.createSchedule({ name: 'Friday report', cron: '0 17 * * 5', action: RUNS }), { ok: true, trigger }],
      ['updateSchedule', () => api.updateSchedule('friday-report', { action: RUNS }, 's1'), { ok: true, trigger }],
      ['createEvent', () => api.createEvent({ pattern: 'AppEvent', action: RUNS }), { ok: true, trigger }],
      ['createRunCompleted', () => api.createRunCompleted({ name: 'After', source_def: 'nightly', action: RUNS }), { ok: true, trigger }],
      ['createHook', () => api.createHook({ name: 'h', event: 'SessionStart', provider: 'run-workflow', provider_config: RUNS.config }), { ok: true, trigger: { ...trigger, kind: 'lifecycle' } }],
      ['updateHook', () => api.updateHook('h', { provider: 'run-workflow', provider_config: RUNS.config }, 'h1'), { ok: true, trigger: { ...trigger, kind: 'lifecycle' } }],
    ]

    it.each(writers)('%s sends the yes with what each question showed', async (_name, write, ok) => {
      const bodies = gateway(ok)
      await write()
      expect(confirmSpy).toHaveBeenCalledTimes(2)
      expect(bodies).toHaveLength(3)
      expect(bodies[0].confirm).toBeUndefined()
      expect(bodies[0].shown).toBeUndefined()
      expect(bodies[1]).toMatchObject({ confirm: true, shown: SHOWN })
      expect(bodies[2]).toMatchObject({ confirm: true, shown: AGAIN })
      // The same write each time, only consented.
      const { confirm: _c, shown: _s, ...resent } = bodies[2]
      expect(resent).toEqual(bodies[0])
    })
  })
})
