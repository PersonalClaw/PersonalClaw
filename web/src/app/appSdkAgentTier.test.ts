// @vitest-environment jsdom
/**
 * An app's background task runs at the app's agent tier, or at a narrower one the task asks for,
 * and a task that asks for more is refused with a sentence the app can show.
 *
 * `permissions.agent` names a tier (`text`, `read`, `tools`). The client an app bundle imports
 * sends the tier a task asks for, and when the gateway refuses it (`403 agent_tier_exceeded`) the
 * rejection is an `AppPermissionError` carrying the gateway's own words — not the "[object Object]"
 * the old client read off the platform envelope. The fake gateway below answers the way
 * `handlers/apps.api_app_agent_run` does for an app holding the `text` tier.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../lib/api'
import { AppPermissionError, createAgentTask } from './appSdk'

const APP = 'probe-notes'
const RUN = `/api/apps/${APP}/agent-run`
const TIERS = ['text', 'read', 'tools']

/** The gateway's half, for an app that holds the `text` tier. */
function fakeGateway() {
  const sent: Record<string, unknown>[] = []
  const json = (status: number, body: unknown) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url === `/api/apps/${APP}/token`) return json(200, { token: 'app-token', expires_in: 3600 })
    if (url === RUN && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>
      sent.push(body)
      const tier = body.tier ?? 'text'
      if (!TIERS.includes(String(tier))) {
        return json(400, { error: { code: 'agent_tier_unknown', message: '"tier" must be "text", "read" or "tools"' } })
      }
      if (tier !== 'text') {
        return json(403, {
          error: {
            code: 'agent_tier_exceeded',
            message: `This task needs the \`agent\` permission at the "${String(tier)}" tier, and this app's is "text".`,
          },
        })
      }
      return json(202, { id: 'run-1', task: body.task, status: 'running', tier })
    }
    if (url === `${RUN}/run-1`) return json(200, { id: 'run-1', done: true, result: 'Two lines.' })
    return new Response('not found', { status: 404 })
  })
  return { fetch, sent }
}

let gateway: ReturnType<typeof fakeGateway>
const realFetch = globalThis.fetch
beforeEach(() => {
  gateway = fakeGateway()
  globalThis.fetch = gateway.fetch as unknown as typeof fetch
})
afterEach(() => { globalThis.fetch = realFetch })

describe("an app's task asks for a tier, and the gateway's answer reaches the app", () => {
  it('runs at the app’s own tier when the task names none', async () => {
    const res = await createAgentTask(APP).run('Summarise these notes.')
    expect(res.result).toBe('Two lines.')
    expect(gateway.sent[0].tier).toBeUndefined()
  })

  it('sends the tier a task asks for', async () => {
    await createAgentTask(APP).start('Summarise these notes.', { tier: 'text' })
    expect(gateway.sent[0].tier).toBe('text')
  })

  it('a task that asks for more than its app holds is refused with the gateway’s sentence', async () => {
    const refusal = await createAgentTask(APP).start('Read my notes.', { tier: 'read' }).catch((e: unknown) => e)
    expect(refusal).toBeInstanceOf(AppPermissionError)
    expect((refusal as Error).message).toContain('"read"')
    expect((refusal as Error).message).toContain('"text"')
  })

  it('any other refusal keeps its status and code', async () => {
    const refusal = await createAgentTask(APP)
      .start('Summarise these notes.', { tier: 'everything' as never })
      .catch((e: unknown) => e)
    expect(refusal).toBeInstanceOf(ApiError)
    expect((refusal as ApiError).code).toBe('agent_tier_unknown')
    expect((refusal as ApiError).status).toBe(400)
  })
})
