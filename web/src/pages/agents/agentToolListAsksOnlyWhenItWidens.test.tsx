/**
 * Widening an agent's tool list asks the owner, in the gateway's words, when the agent is saved; a
 * change that only narrows it asks nothing.
 *
 * A tick in the Tools list is a standing grant, but a tick alone cannot say which way it moves it:
 * an empty list is every tool, so the first tick on one NARROWS the agent to that tool, while a tick
 * on a list that has entries widens it. The editor used to ask "Let this agent call bash?" on the
 * tick of a destructive tool, which asked on exactly that narrowing first tick, and asked nothing
 * when a save added a tool to a list that had entries or emptied one (every tool). The gateway now
 * decides from the stored list: a save that lets the agent call more is answered
 * `confirmation_required` and the owner is asked through the one consent flow
 * (`lib/securityConsent.ts`); the editor asks nothing of its own.
 *
 * Driven through the real `api.updateAgent` over a stubbed `fetch` standing in for the gateway, so
 * what is asserted is the wire: the first save carries no consent, and only an accepted dialog sends
 * a second one that does.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { SavedAgent } from '../../lib/api'

const confirmSpy = vi.hoisted(() => vi.fn(async (_opts: unknown) => true))
vi.mock('../../ui/dialog', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../ui/dialog')>()),
  confirm: (opts: unknown) => confirmSpy(opts),
}))

const reads = vi.hoisted(() => ({
  agents: vi.fn(),
  routingStatus: vi.fn(async () => ({ enabled: true, muted: [], dismissals: {} })),
  mcpActive: vi.fn(async () => []),
  agentHooks: vi.fn(async () => ({ hooks: {}, waiting: [] })),
  chatModels: vi.fn(async () => []),
  skills: vi.fn(async () => []),
  hooks: vi.fn(async () => []),
  tools: vi.fn(async () => [
    { name: 'read_file', provider: 'core', risk_level: 'safe' },
    { name: 'bash', provider: 'core', risk_level: 'destructive' },
  ]),
}))
vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  // `updateAgent` stays the real one, so the save goes over the wire to the gateway below.
  return { ...mod, api: { ...mod.api, ...reads } }
})

import { NativeAgentDetail } from './AgentDetail'

const CONSENT = 'A wider tool list lets this agent call tools it could not call before, in every chat and run of it.'

const reply = (status: number, o: unknown) =>
  new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } })

/** A gateway holding the agent with *stored* tools: a save that adds a tool it lacks, without
 *  consent, is answered with the question the gateway asks, the change line included. */
function gateway(stored: string[]) {
  const saves: Array<Record<string, unknown>> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body)) as Record<string, unknown>
    saves.push(body)
    const tools = (body.tools as string[]) ?? []
    const added = tools.filter((t) => !stored.includes(t))
    const wider = stored.length > 0 && (tools.length === 0 || added.length > 0)
    if (wider && body.confirm !== true) {
      return reply(400, {
        error: {
          code: 'confirmation_required',
          message: 'send {"confirm": true} to confirm',
          detail: {
            field: 'agents.researcher.tools', consent: CONSENT, title: 'Loosen a security setting?',
            change: added.map((t) => `Adds “${t}”`).join('; '),
          },
        },
      })
    }
    expect(String(url)).toContain('/api/agents/researcher')
    return reply(200, { ok: true, name: 'researcher', revision: 'r2' })
  }))
  return saves
}

function agentWith(tools: string[]): SavedAgent {
  return {
    name: 'researcher', provider: '', description: 'reads papers', system_prompt: '', model: '',
    approval_mode: '', skills: [], tools, triggers: [], revision: 'r1',
  }
}

async function editAndTickBash(agent: SavedAgent) {
  reads.agents.mockResolvedValue({ agents: [agent], default_agent: 'PersonalClaw' })
  const onSaved = vi.fn()
  render(
    <NativeAgentDetail agent={agent} isDefault={false} onSaved={onSaved} onDeleted={vi.fn()}
      onSetDefault={vi.fn()} editing onEditingChange={vi.fn()} />,
  )
  const bash = await screen.findByRole('button', { name: /^bash/ })
  await act(async () => { fireEvent.click(bash) })
  return { onSaved }
}

async function save() {
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save/ })) })
}

beforeEach(() => {
  sessionStorage.clear()
  confirmSpy.mockReset()
  confirmSpy.mockImplementation(async () => true)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe("an agent's tool list asks only when a save widens it", () => {
  it('the first tick on an empty list narrows it, so neither the tick nor the save asks', async () => {
    const saves = gateway([])
    const { onSaved } = await editAndTickBash(agentWith([]))
    // 🔴 Red before: this tick opened "Let this agent call bash?" though it narrows the agent.
    expect(confirmSpy).not.toHaveBeenCalled()

    await save()
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(saves).toHaveLength(1)
    expect(saves[0]).toMatchObject({ tools: ['bash'] })
    expect(saves[0]).not.toHaveProperty('confirm')
  })

  it('adding a tool to a list the agent has asks at the save, in the gateway’s words', async () => {
    const saves = gateway(['read_file'])
    const { onSaved } = await editAndTickBash(agentWith(['read_file']))
    expect(confirmSpy).not.toHaveBeenCalled()

    await save()
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    const asked = confirmSpy.mock.calls[0][0] as { title: string; body: string }
    expect(asked.title).toBe('Loosen a security setting?')
    expect(asked.body).toBe(`${CONSENT}\n\nAdds “bash”`)
    expect(saves.map((s) => [s.tools, s.confirm])).toEqual([
      [['read_file', 'bash'], undefined],
      [['read_file', 'bash'], true],
    ])
  })

  it('a declined question stores nothing, and the editor keeps the change', async () => {
    confirmSpy.mockImplementation(async () => false)
    const saves = gateway(['read_file'])
    const { onSaved } = await editAndTickBash(agentWith(['read_file']))

    await save()
    await screen.findByText('Not changed — you kept the current setting.')
    expect(onSaved).not.toHaveBeenCalled()
    expect(saves).toHaveLength(1)
    expect(screen.getByRole('button', { name: /^bash/ }).getAttribute('aria-pressed')).toBe('true')
  })
})
