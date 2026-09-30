import { describe, expect, it, vi, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ActionConfig, coerceActionConfig } from './ActionConfig'
import type { ActionProvider } from '../../lib/api'

// ── The files an automation may change are set where its action is ──────────────────────────────
//
// An automation made to summarise each new PDF into a kitchen note ran read-only, as every
// automation that fires on its own does, so it could not write the note it was made for, and its
// Allow never said so. The only way to let it write was `capability: mutating`: any file, any
// command. An agent-starting action now names the files its job changes (`writes`), and the run may
// write those and nothing else. This is where she names them: the form the shipped schema renders.

vi.mock('../prompts/promptWidgets', () => ({ usePromptWidgets: () => ({ prompts: [], widgets: {} }) }))

/** The action as it ships: its schema read from the manifest the gateway serves. */
function shipped(app: string, name: string): ActionProvider {
  const path = join(__dirname, `../../../../src/personalclaw/apps/native/${app}/app.json`)
  const manifest = JSON.parse(readFileSync(path, 'utf8'))
  return {
    name, display_name: manifest.displayName, supports_blocking: false,
    settingsSchema: manifest.provider.settingsSchema,
  }
}

const RUN_PROMPT = shipped('run-prompt-action', 'run-prompt')
const INVOKE_AGENT = shipped('invoke-agent-action', 'invoke-agent')

afterEach(() => cleanup())

describe('the files an automation may change', () => {
  it.each([[RUN_PROMPT], [INVOKE_AGENT]])('🔑 %# are named on the form, one path each, and saved as a list', (provider) => {
    const onConfig = vi.fn()
    render(<ActionConfig providers={[provider]} provider={provider.name} config={{}}
      onProvider={vi.fn()} onConfig={onConfig} vars={[]} />)

    // On the form itself, not behind Advanced: it is what lets the job be done.
    expect(screen.getByText('Files it may change')).toBeInTheDocument()
    expect(screen.getByText(/its agent may write to these and to nothing else/)).toBeInTheDocument()

    const field = screen.getByRole('textbox', { name: 'Add a file it may change' })
    fireEvent.change(field, { target: { value: '~/Notes/kitchen.md' } })
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(onConfig).toHaveBeenLastCalledWith({ writes: ['~/Notes/kitchen.md'] })
  })

  it('a saved list shows as its paths and is sent as a list, not as text', () => {
    const config = { message: 'Summarise it into my kitchen note', writes: ['~/Notes/kitchen.md'] }
    render(<ActionConfig providers={[RUN_PROMPT]} provider="run-prompt" config={config}
      onProvider={vi.fn()} onConfig={vi.fn()} vars={[]} />)
    expect(screen.getByText('~/Notes/kitchen.md')).toBeInTheDocument()
    expect(coerceActionConfig([RUN_PROMPT], 'run-prompt', config)).toEqual({ config })
  })
})
