import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { useState } from 'react'
import type { ActionProvider } from '../../lib/api'

// ── A trigger's "Run workflow" action names its workflow (F-27) ───────────────────────────────────
//
// The action was registered against the v2 engine without the manifest this form reads, so
// `/api/action-providers` served it an empty `settingsSchema`: the form said "This action takes
// no configuration.", the trigger saved with no workflow, and every fire failed with
// "run-workflow requires a `workflow` name". The form's own comment said the provider was gone.
//
// The schema here is the bundled manifest READ FROM THE SOURCE, because that file's
// `settingsSchema` is exactly what the gateway serves for this action — a hand-copied schema would
// pass on a form the real manifest cannot drive.

vi.mock('../prompts/promptWidgets', () => ({ usePromptWidgets: () => ({ prompts: [], widgets: {} }) }))

const { READS } = vi.hoisted(() => ({ READS: { defs: [] as string[] } }))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    workflowDefs: () => Promise.resolve({
      defs: [{ name: 'brief', description: 'Three lines about a topic', source: 'user', version: 1, tags: [], provider: 'user' }],
      total: 1,
    }),
    workflowDef: (name: string) => {
      READS.defs.push(name)
      return Promise.resolve({
        provider: 'user',
        definition: {
          name,
          root: { kind: 'infer', id: 'write' },
          inputs: { topic: { type: 'string', required: true }, tone: { type: 'string', default: 'plain' } },
        },
      })
    },
  },
}))

const { ActionConfig } = await import('./ActionConfig')

const MANIFEST = JSON.parse(readFileSync(
  join(process.cwd(), '..', 'src', 'personalclaw', 'apps', 'native', 'run-workflow-action', 'app.json'),
  'utf8',
))
const RUN_WORKFLOW: ActionProvider = {
  name: 'run-workflow', display_name: 'Run Workflow', internal: false, supports_blocking: false,
  settingsSchema: MANIFEST.provider.settingsSchema,
}

let saved: Record<string, unknown> = {}
function Form() {
  const [config, setConfig] = useState<Record<string, unknown>>({})
  return (
    <ActionConfig providers={[RUN_WORKFLOW]} provider="run-workflow" config={config} vars={[]}
      onProvider={() => {}} onConfig={(c) => { saved = c; setConfig(c) }} />
  )
}

beforeEach(() => { saved = {}; READS.defs = [] })

describe('the Run workflow action asks which workflow it runs', () => {
  it('offers your workflows in a picker', async () => {
    render(<Form />)
    fireEvent.click(await screen.findByRole('button', { name: /pick a workflow/i }))
    const options = screen.getAllByRole('option').map((o) => o.textContent ?? '')
    expect(options.some((t) => t.includes('brief'))).toBe(true)
  })

  it('says to pick a workflow before it offers any inputs', async () => {
    render(<Form />)
    expect(await screen.findByText('Pick a workflow to set its inputs.')).toBeInTheDocument()
  })

  it("renders the chosen workflow's inputs as fields and saves them into the action", async () => {
    render(<Form />)
    fireEvent.click(await screen.findByRole('button', { name: /pick a workflow/i }))
    fireEvent.click(screen.getByRole('option', { name: /brief/ }))
    // `tone`'s declared default is seeded as the fields appear, so the default the field shows is
    // the one saved.
    await waitFor(() => expect(saved).toMatchObject({ workflow: 'brief', inputs: { tone: 'plain' } }))
    expect(screen.getByLabelText('tone')).toHaveValue('plain')
    fireEvent.change(screen.getByLabelText('topic'), { target: { value: 'tides' } })
    expect(saved).toMatchObject({ workflow: 'brief', inputs: { topic: 'tides', tone: 'plain' } })
    expect(READS.defs).toContain('brief')
  })
})
