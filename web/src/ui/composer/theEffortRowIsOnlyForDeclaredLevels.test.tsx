import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { ComposerData } from './types'

// ── One pill for the agent and the model, with an effort only where one is declared ─────────────
//
// The composer drew three pills, "Agent", "Auto" and "Default", and the third offered Low, Medium,
// High and Max for every native model, whether or not the model takes an effort, so it could be set
// and change nothing. One pill now picks the agent and the model, and its menu has an effort row
// under the chosen model only when that model declares its levels: an agent CLI through its
// runtime's `supported_efforts`, a native model through its provider's record of it (`efforts` on
// the composer's model list). A switch to a model without the chat's level sets the effort back to
// Default, and the pill says so.

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: new Proxy(real.api, {
      get: (target, key) => (key === 'dashboardConfig'
        ? () => Promise.resolve({ send_on_enter: true })
        : (target as Record<string | symbol, unknown>)[key]),
    }),
  }
})

const { AgentModelPill } = await import('./controls')
const { Composer } = await import('../Composer')

const DATA = {
  agents: [{ name: 'researcher' }],
  providers: [],
  models: [
    { name: 'Acme:deep', model_name: 'Deep', provider: 'Acme', description: '', efforts: ['low', 'high'] },
    { name: 'Acme:flat', model_name: 'Flat', provider: 'Acme', description: '' },
    { name: 'Acme:wide', model_name: 'Wide', provider: 'Acme', description: '', efforts: ['low', 'medium', 'high', 'max'] },
  ],
  discovered: {
    'acp:example-cli': [
      { id: 'coder', name: 'Example coder', runtime: 'acp:example-cli', provider_agent: 'coder', models: ['fast'], supported_efforts: [{ value: 'low', label: 'Low' }, { value: 'high', label: 'High' }] },
      { id: 'plain', name: 'Plain CLI', runtime: 'acp:example-cli', provider_agent: 'plain', models: [], supported_efforts: [] },
    ],
  },
} as unknown as ComposerData

type Pick = { agent?: string; model?: string; effort?: string }

function pill({ agent = 'researcher', model = 'Auto', effort = '' }: Pick = {}, onSelect = vi.fn()) {
  render(<AgentModelPill data={DATA} agent={agent} model={model} effort={effort as never} onSelect={onSelect} />)
  fireEvent.click(screen.getByRole('button', { name: /^Agent and model: / }))
  return onSelect
}

const effortRow = () => screen.queryByRole('button', { name: /^Effort: / })
/** The levels the effort row lists, opened. */
function levels(): string[] {
  fireEvent.click(effortRow()!)
  return within(screen.getByRole('group', { name: 'Effort' })).getAllByRole('button').map((b) => b.textContent ?? '')
}

describe('the effort row is there only for a model that declares levels', () => {
  it('a native model that declares levels lists exactly those, after Default', () => {
    pill({ model: 'Acme:deep' })
    expect(effortRow()?.textContent).toBe('Effort: Default')
    expect(levels()).toEqual(['Default', 'Low', 'High'])
  })

  it('a native model that declares none has no effort row', () => {
    pill({ model: 'Acme:flat' })
    expect(effortRow()).toBeNull()
  })

  it('Auto is no model that declares levels, so it has none either', () => {
    pill({ model: 'Auto' })
    expect(effortRow()).toBeNull()
  })

  it("an agent CLI's row lists exactly its runtime's declared levels", () => {
    pill({ agent: 'Example coder', model: 'Auto' })
    expect(levels()).toEqual(['Default', 'Low', 'High'])
  })

  it('an agent CLI whose runtime declares none has no effort row', () => {
    pill({ agent: 'Plain CLI', model: 'Auto' })
    expect(effortRow()).toBeNull()
  })

  it('the row sits under the chosen model and names the chat’s level', () => {
    pill({ model: 'Acme:deep', effort: 'high' })
    const row = effortRow()!
    expect(row.textContent).toBe('Effort: High')
    // Directly after the chosen model's row, before the next model.
    const deep = screen.getByText('Deep').closest('button')!
    expect(deep.nextElementSibling?.contains(row), 'the effort row follows the chosen model').toBe(true)
  })

  it('picking a level sets it for the chat', () => {
    const onSelect = pill({ model: 'Acme:deep' })
    fireEvent.click(effortRow()!)
    fireEvent.click(within(screen.getByRole('group', { name: 'Effort' })).getByRole('button', { name: 'High' }))
    expect(onSelect).toHaveBeenCalledWith({ reasoning: 'high' })
  })
})

describe('a switch to a model without the chat’s level falls back to Default and says so', () => {
  it('a model with no such level: Default, and the pill names the model and the level', () => {
    const onSelect = pill({ model: 'Acme:wide', effort: 'max' })
    fireEvent.click(screen.getByText('Deep'))
    expect(onSelect).toHaveBeenCalledWith({ model: 'Acme:deep', reasoning: '' })
    expect(screen.getByRole('status').textContent).toBe('Effort set to Default: Deep has no Max')
  })

  it('a model with no levels at all falls back the same way', () => {
    const onSelect = pill({ model: 'Acme:deep', effort: 'high' })
    fireEvent.click(screen.getByText('Flat'))
    expect(onSelect).toHaveBeenCalledWith({ model: 'Acme:flat', reasoning: '' })
    expect(screen.getByRole('status').textContent).toBe('Effort set to Default: Flat has no High')
  })

  it('a model that has the level keeps it, and the pill says nothing', () => {
    const onSelect = pill({ model: 'Acme:wide', effort: 'high' })
    fireEvent.click(screen.getByText('Deep'))
    expect(onSelect).toHaveBeenCalledWith({ model: 'Acme:deep' })
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('a chat on Default switches without a word', () => {
    const onSelect = pill({ model: 'Acme:deep' })
    fireEvent.click(screen.getByText('Flat'))
    expect(onSelect).toHaveBeenCalledWith({ model: 'Acme:flat' })
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('an agent switch whose runtime lacks the level falls back too', () => {
    const onSelect = pill({ model: 'Acme:wide', effort: 'max' })
    fireEvent.click(screen.getByText('Example coder'))
    expect(onSelect).toHaveBeenCalledWith({ agent: 'Example coder', reasoning: '' })
    expect(screen.getByRole('status').textContent).toBe('Effort set to Default: Example coder has no Max')
  })
})

// A chat can already be on an agent and model that lack its level: one stored when every native
// model offered Low to Max, a starter's level on a chat at Auto, or another tab's. The
// pill shows such a chat no effort row, so the level was sent each turn with no control to see or
// change it. The chat now runs at Default, as a switch to that model would set it, and the pill says
// so beside itself.
describe('a chat already on a model without its level runs at Default, and the pill says so', () => {
  /** The pill as a chat opens on it: mounted, not opened. */
  function mounted({ agent = 'researcher', model = 'Auto', effort = '' }: Pick = {}, data: ComposerData = DATA) {
    const onSelect = vi.fn()
    const view = render(<AgentModelPill data={data} agent={agent} model={model} effort={effort as never} onSelect={onSelect} />)
    return { onSelect, view }
  }
  const note = () => screen.queryByRole('status')?.textContent ?? null

  it.each([
    ['on Auto', { model: 'Auto', effort: 'high' }, 'Effort set to Default: Auto has no High'],
    ['on a native model that declares none', { model: 'Acme:flat', effort: 'high' }, 'Effort set to Default: Flat has no High'],
    ['on a native model without that level', { model: 'Acme:deep', effort: 'max' }, 'Effort set to Default: Deep has no Max'],
    ['on an agent CLI whose runtime declares none', { agent: 'Plain CLI', model: 'Auto', effort: 'high' }, 'Effort set to Default: Plain CLI has no High'],
  ])('%s', (_, sel, said) => {
    const { onSelect } = mounted(sel)
    expect(onSelect.mock.calls).toEqual([[{ reasoning: '' }]])
    expect(note()).toBe(said)
  })

  it('writes Default once, not on every render', () => {
    const { onSelect, view } = mounted({ model: 'Auto', effort: 'high' })
    view.rerender(<AgentModelPill data={{ ...DATA }} agent="researcher" model="Auto" effort={'high' as never} onSelect={onSelect} />)
    expect(onSelect).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['a native model that declares the level', { model: 'Acme:deep', effort: 'high' }],
    ['an agent CLI whose runtime declares the level', { agent: 'Example coder', model: 'Auto', effort: 'high' }],
    ['a chat on Default', { model: 'Auto', effort: '' }],
  ])('%s keeps its effort, without a word', (_, sel) => {
    const { onSelect } = mounted(sel)
    expect(onSelect).not.toHaveBeenCalled()
    expect(note()).toBeNull()
  })

  it('nothing is judged before the lists are read, when every model would look as if it declared none', () => {
    const { onSelect, view } = mounted({ model: 'Acme:deep', effort: 'high' }, { ...DATA, models: [], ready: false })
    expect(onSelect).not.toHaveBeenCalled()
    view.rerender(<AgentModelPill data={{ ...DATA, ready: true }} agent="researcher" model="Acme:deep" effort={'high' as never} onSelect={onSelect} />)
    expect(onSelect, 'Deep declares High').not.toHaveBeenCalled()
    view.rerender(<AgentModelPill data={{ ...DATA, ready: true }} agent="researcher" model="Auto" effort={'high' as never} onSelect={onSelect} />)
    expect(onSelect.mock.calls).toEqual([[{ reasoning: '' }]])
  })

  it('a pill that offers no effort leaves the chat’s effort to its host', () => {
    const onSelect = vi.fn()
    render(<AgentModelPill data={DATA} agent="researcher" model="Auto" effort={'high' as never} onSelect={onSelect} parts={{ agent: true, model: true, effort: false }} />)
    expect(onSelect).not.toHaveBeenCalled()
  })
})

describe('the composer footer has one agent-and-model control and no separate effort control', () => {
  const CONTROLS = { agent: true, model: true, approval: false, reasoning: true, attach: false, mic: false, optimize: false }

  it.each([
    ['a native model that declares levels', { agent: 'researcher', model: 'Acme:deep' }],
    ['a native model that declares none', { agent: 'researcher', model: 'Acme:flat' }],
    ['an agent CLI that declares levels', { agent: 'Example coder', model: 'Auto' }],
  ])('%s', (_, sel) => {
    render(
      <Composer value="" onChange={() => {}} onSend={() => {}} controls={CONTROLS} data={DATA}
        selection={{ ...sel, approval: 'normal', taskMode: 'agent', reasoning: '' }} />,
    )
    expect(screen.getAllByRole('button', { name: /^Agent and model: / })).toHaveLength(1)
    expect(screen.queryByRole('button', { name: /^Agent(: |$)/ }), 'no separate agent pill').toBeNull()
    expect(screen.queryByRole('button', { name: /^Model(: |$)/ }), 'no separate model pill').toBeNull()
    expect(screen.queryByRole('button', { name: /Reasoning effort|^Effort/ }), 'no effort control outside the pill').toBeNull()
  })
})
