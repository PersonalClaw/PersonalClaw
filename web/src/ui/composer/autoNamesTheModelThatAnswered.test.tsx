import { describe, it, expect, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fireEvent, render, screen } from '@testing-library/react'
import { ModelPill } from './controls'
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
//   the model pill's Auto     "Auto · deep-coder", its title the whole sentence
//   the pill's Auto row       the chain the NEXT turn takes ("Your Code & tools chain")
//
// The names are invented.

const SERVED = { model: 'deep-coder', chosen: 'from your Code & tools chain' }

describe("the model pill's Auto names the model the latest turn ran on", () => {
  it('says Auto alone before any turn has named one (the control for the legs below)', () => {
    render(<ModelPill value="Auto" onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Model: Auto' })).toBeTruthy()
  })

  it('names the served model, and its title says how that model was chosen', () => {
    render(<ModelPill value="Auto" onSelect={vi.fn()} served={SERVED} />)
    const pill = screen.getByRole('button', { name: 'Model: Auto · deep-coder' })
    expect(pill.getAttribute('title')).toBe('Auto — the latest turn ran on deep-coder, from your Code & tools chain.')
  })

  it('a model picked for the chat names itself, not the latest turn', () => {
    const data = { models: [{ name: 'plan-models:flat-chat', model_name: 'flat-chat', provider: 'plan-models' }] }
    render(<ModelPill data={data as never} value="plan-models:flat-chat" onSelect={vi.fn()} served={SERVED} />)
    const pill = screen.getByRole('button', { name: 'Model: flat-chat' })
    expect(pill.getAttribute('title')).toBeNull()
  })

  it('its Auto row names the chain the next turn takes', () => {
    render(<ModelPill value="Auto" onSelect={vi.fn()} autoChain="Code & tools" />)
    fireEvent.click(screen.getByRole('button', { name: 'Model: Auto' }))
    expect(screen.getByText('Your Code & tools chain')).toBeTruthy()
  })

  it('an agent CLI brings its own model, so its Auto row names no chain of ours', () => {
    const data = { models: [], discovered: { 'acp:example-cli': [{ id: 'coder', name: 'Example coder', runtime: 'acp:example-cli', models: [] }] } }
    render(<ModelPill data={data as never} agent="Example coder" value="Auto" onSelect={vi.fn()} autoChain="Code & tools" />)
    fireEvent.click(screen.getByRole('button', { name: 'Model: Auto' }))
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

// The live half is folded inside `ChatPage.tsx` (a page that owns a socket and is not mountable
// here), so it is asserted as source, in the attribute form the page writes it.
describe('the chat page hands the served model and the chain to both readers', () => {
  const page = readFileSync(join(__dirname, '../../pages/ChatPage.tsx'), 'utf8')

  it("the live stats frame sets the turn's served model", () => {
    expect(page).toMatch(/if \(kind === 'stats'\) \{\s*const served = servedModelOf\(/)
    expect(page).toContain('next[i] = { ...next[i], servedModel: served }')
  })

  it('the composer is given the latest served model and the chain, and the chip its turn\'s', () => {
    expect(page).toContain('servedModel={lastServedModel(turns)} autoChain={autoChain}')
    expect(page).toContain('servedModel={turn.servedModel}')
    expect(page).toContain('served={servedModel}')
  })

  it('the chain is read from session detail and from setting the working directory', () => {
    expect(page).toContain("setAutoChain(d.auto_chain ?? '')")
    expect(page).toContain("if (typeof r.auto_chain === 'string') setAutoChain(r.auto_chain)")
  })
})
