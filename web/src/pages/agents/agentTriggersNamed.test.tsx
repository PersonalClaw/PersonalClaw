/**
 * #629 — the agent detail panel names its trigger bindings.
 *
 * Skills/Tools bindings store labels, so the shared Caps renderer printed them
 * fine; a trigger binding stores the hook's raw id, and the same renderer
 * produced `e4ec861a` where the picker had shown a name + event. The panel now
 * resolves ids through api.hooks() — the endpoint the picker built its options
 * from — and renders "name · event". Honesty contract: a LOADED map that lacks
 * the id marks the binding as dangling ("trigger no longer exists"); a failed
 * or skipped fetch renders the raw id and claims nothing.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'

const hooksMock = vi.fn()

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return { ...mod, api: { ...mod.api, hooks: (...a: unknown[]) => hooksMock(...a) } }
})

import { NativeAgentDetail } from './AgentDetail'

const HOOK = {
  id: 'e4ec861a', name: 'Log every lesson the translator pipeline learns', event: 'MemoryWrite',
  matcher: '', provider: 'shell', provider_config: {}, timeout: 30, enabled: true,
  last_run: 0, last_status: '', run_count: 0, used_by: [],
}

function agentWith(over: Record<string, unknown> = {}) {
  return {
    name: 'termbase-auditor', description: '', model: '', approval_mode: '',
    system_prompt: '', skills: ['knowledge-grounding'], tools: [], triggers: ['e4ec861a'],
    ...over,
  } as never
}

function mount(agent = agentWith()) {
  return render(
    <NativeAgentDetail
      agent={agent} isDefault={false} editing={false}
      onSaved={() => {}} onDeleted={() => {}} onSetDefault={() => {}} onEditingChange={() => {}}
    />,
  )
}

beforeEach(() => { hooksMock.mockReset() })
afterEach(() => cleanup())

describe('agent detail names its trigger bindings (#629)', () => {
  it('resolves the bound id to the picker\u2019s own "name \u00b7 event" and hides the hex', async () => {
    hooksMock.mockResolvedValue([HOOK])
    mount()
    await waitFor(() => {
      expect(screen.getByText(/Log every lesson the translator pipeline learns \u00b7 MemoryWrite/)).toBeTruthy()
    })
    expect(screen.queryByText('e4ec861a')).toBeNull()
    // Sibling rows are untouched: a skill's stored value IS its label.
    expect(screen.getByText('knowledge-grounding')).toBeTruthy()
  })

  it('marks a binding whose trigger was deleted as dangling, keeping the id legible', async () => {
    hooksMock.mockResolvedValue([]) // loaded fine — the id genuinely resolves to nothing
    mount()
    await waitFor(() => {
      expect(screen.getByText(/trigger no longer exists/)).toBeTruthy()
    })
    // The raw id stays visible so the user can identify what to unbind.
    expect(screen.getByText('e4ec861a')).toBeTruthy()
  })

  it('claims nothing when the trigger list could not be fetched', async () => {
    hooksMock.mockRejectedValue(new Error('boom'))
    mount()
    // Raw id renders (as before the fix) …
    expect(await screen.findByText('e4ec861a')).toBeTruthy()
    // … but no "no longer exists" claim: a failed fetch cannot know that.
    expect(screen.queryByText(/trigger no longer exists/)).toBeNull()
  })

  it('does not fetch triggers for an agent with no bindings', async () => {
    hooksMock.mockResolvedValue([HOOK])
    mount(agentWith({ triggers: [] }))
    // The skills row proves the panel rendered.
    expect(await screen.findByText('knowledge-grounding')).toBeTruthy()
    expect(hooksMock).not.toHaveBeenCalled()
  })
})
