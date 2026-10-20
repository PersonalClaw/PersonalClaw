import { describe, it, expect, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fireEvent, render, screen } from '@testing-library/react'
import { AgentModelPill } from './controls'
import { ContextLedger } from '../../pages/chat/ContextLedger'
import { hydrateTurns, lastServedModel, type HistMsg } from '../../pages/chat/chatTypes'

// ── A turn that never said which binding it ran on ─────────────────────────────────────
//
// Settings → Models offered Code & tools for code work, and every chat turn ran on Chat. With
// Code & tools on one provider and Chat on another, a turn over her repository went to Chat's
// provider, and nothing on the page said so: the pill read "Auto" and the turn's details named
// no model, so a binding passed over read exactly like one that served.
//
// A chat working in a folder of its own now runs on Code & tools, and the turn says what it ran
// on: the gateway names the model that answered and how it was chosen on the turn's record
// (`meta.turn_telemetry.model` / `.chosen`) and its live `stats` frame. Three places read it:
//
//   the turn's details chip   "on deep-coder", and opened "Answered by: deep-coder, from …"
//   the pill's model half     "Auto · deep-coder", its title the whole sentence
//   the pill's Auto row       the chain the NEXT turn takes ("Your Code & tools chain")
//
// The names are invented.

const SERVED = { model: 'deep-coder', chosen: 'from your Code & tools chain' }

describe("the model pill's Auto names the model the latest turn ran on", () => {
  it('says Auto alone before any turn has named one (the control for the legs below)', () => {
    render(<AgentModelPill agent="researcher" model="Auto" effort="" onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Agent and model: researcher · Auto' })).toBeTruthy()
  })

  it('names the served model, and its title says how that model was chosen', () => {
    render(<AgentModelPill agent="researcher" model="Auto" effort="" onSelect={vi.fn()} served={SERVED} />)
    const pill = screen.getByRole('button', { name: 'Agent and model: researcher · Auto · deep-coder' })
    expect(pill.getAttribute('title')).toBe('Auto — the latest turn ran on deep-coder, from your Code & tools chain.')
  })

  it('a model picked for the chat names itself, not the latest turn', () => {
    const data = { models: [{ name: 'plan-models:flat-chat', model_name: 'flat-chat', provider: 'plan-models' }] }
    render(<AgentModelPill data={data as never} agent="researcher" model="plan-models:flat-chat" effort="" onSelect={vi.fn()} served={SERVED} />)
    const pill = screen.getByRole('button', { name: 'Agent and model: researcher · flat-chat' })
    expect(pill.getAttribute('title')).toBeNull()
  })

  it('its Auto row names the chain the next turn takes', () => {
    render(<AgentModelPill agent="researcher" model="Auto" effort="" onSelect={vi.fn()} autoChain="Code & tools" />)
    fireEvent.click(screen.getByRole('button', { name: 'Agent and model: researcher · Auto' }))
    expect(screen.getByText('Your Code & tools chain')).toBeTruthy()
  })

  it('an agent CLI brings its own model, so its Auto row names no chain of ours', () => {
    const data = { models: [], discovered: { 'acp:example-cli': [{ id: 'coder', name: 'Example coder', runtime: 'acp:example-cli', models: [] }] } }
    render(<AgentModelPill data={data as never} agent="Example coder" model="Auto" effort="" onSelect={vi.fn()} autoChain="Code & tools" />)
    fireEvent.click(screen.getByRole('button', { name: 'Agent and model: Example coder · Auto' }))
    expect(screen.queryByText('Your Code & tools chain')).toBeNull()
    expect(screen.getByText('Use-case chain (Settings → Models)')).toBeTruthy()
  })
})

describe("the turn's details chip names the model that answered it", () => {
  it('collapsed, it says the model; opened, how it was chosen', () => {
    render(<ContextLedger stats="Turn complete: 1 events, 0 tool calls" served={SERVED} />)
    const chip = screen.getByRole('button')
    expect(chip.textContent).toContain('on deep-coder')
    fireEvent.click(chip)
    const row = screen.getByText('Answered by:').parentElement?.textContent ?? ''
    expect(row).toBe('Answered by: deep-coder, from your Code & tools chain.')
  })

  it('a turn that named no model says none (the control for the leg above)', () => {
    render(<ContextLedger stats="Turn complete: 1 events, 0 tool calls" />)
    const chip = screen.getByRole('button')
    expect(chip.textContent).not.toContain(' on ')
    fireEvent.click(chip)
    expect(screen.queryByText('Answered by:')).toBeNull()
  })
})

describe('a reload reads the served model back from the turn it was recorded on', () => {
  const telemetry = (model: string, chosen: string) => ({ line: `Turn complete: 1 events, 0 tool calls · ${model}, ${chosen}`, model, chosen })
  const transcript: HistMsg[] = [
    { role: 'user', content: 'Run the tests.' },
    { role: 'assistant', content: '12 passed.', meta: { turn_telemetry: telemetry('flat-chat', 'from your Chat chain') } },
    { role: 'user', content: 'Again, in the repository.' },
    { role: 'assistant', content: '12 passed.', meta: { turn_telemetry: telemetry('deep-coder', 'from your Code & tools chain') } },
  ]

  it('each turn keeps its own, and Auto names the latest', () => {
    const turns = hydrateTurns(transcript, false)
    const answers = turns.filter((t) => t.role === 'assistant')
    expect(answers.map((t) => t.servedModel?.model)).toEqual(['flat-chat', 'deep-coder'])
    expect(lastServedModel(turns)).toEqual(SERVED)
  })

  it('a record that names no model leaves the turn with none', () => {
    const turns = hydrateTurns([{ role: 'user', content: 'Hi' }, { role: 'assistant', content: 'Hello.', meta: { turn_telemetry: { line: 'Turn complete: 1 events, 0 tool calls' } } }], false)
    expect(turns[1].servedModel).toBeUndefined()
    expect(lastServedModel(turns)).toBeUndefined()
  })
})

// The live half is folded inside the chat's stream (`chat/useChatStream.ts`, which owns the socket
// and is not mountable here) and handed on by the chat's regions (its composer, transcript and
// header), so it is asserted as source, in the attribute form they write it.
describe('the chat page hands the served model and the chain to both readers', () => {
  const stream = readFileSync(join(__dirname, '../../pages/chat/useChatStream.ts'), 'utf8')

  it("the live stats frame sets the turn's served model", () => {
    expect(stream).toMatch(/if \(kind === 'stats'\) \{\s*const served = servedModelOf\(/)
    expect(stream).toContain('next[i] = { ...next[i], servedModel: served }')
  })

  it('the composer is given the latest served model and the chain, and the chip its turn\'s', () => {
    // The turn's chip is drawn by the transcript region (`chat/transcript/`), and the composer by its own
    // (`chat/composer/`).
    const composer = readFileSync(join(__dirname, '../../pages/chat/composer/ChatComposer.tsx'), 'utf8')
    const transcript = readFileSync(join(__dirname, '../../pages/chat/transcript/Transcript.tsx'), 'utf8')
    const turn = readFileSync(join(__dirname, '../../pages/chat/transcript/AssistantSegments.tsx'), 'utf8')
    expect(composer).toContain('servedModel={lastServedModel(turns)} autoChain={autoChain}')
    expect(transcript).toContain('servedModel={turn.servedModel}')
    expect(turn).toContain('served={servedModel}')
  })

  it('the chain is read from session detail and from setting the working directory', () => {
    expect(stream).toContain("setAutoChain(d.auto_chain ?? '')")
    // Setting the working directory is the chat header's action (`chat/header/useChatHeader.ts`).
    const header = readFileSync(join(__dirname, '../../pages/chat/header/useChatHeader.ts'), 'utf8')
    expect(header).toContain("if (typeof r.auto_chain === 'string') setAutoChain(r.auto_chain)")
  })
})
