import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { AppConfigFields, type SchemaProp } from './appConfigForm'

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      prompts: vi.fn().mockResolvedValue([
        { name: 'daily-brief', description: 'Summarize the day' },
      ]),
    },
  }
})

afterEach(() => cleanup())

describe('App Configure adopts x-meta widgets (#500)', () => {
  it('renders the saved-prompt picker instead of a plain text input', async () => {
    const props: Record<string, SchemaProp> = {
      prompt_id: {
        type: 'string',
        'x-meta': { label: 'Saved Prompt', help: 'Choose a prompt.', widget: 'prompt' },
      },
    }
    const set = vi.fn()

    render(<AppConfigFields appName="run-prompt-action" props={props} cur={{}} set={set} />)

    const field = screen.getByRole('group', { name: 'Saved Prompt' })
    expect(within(field).queryByRole('textbox')).toBeNull()
    fireEvent.click(within(field).getByRole('button', { name: 'Choose a prompt.' }))

    const option = await screen.findByRole('option', { name: /daily-brief/ })
    fireEvent.click(option)
    await waitFor(() => expect(set).toHaveBeenCalledWith('prompt_id', 'daily-brief'))
  })

  it('keeps sensitive metadata on the canonical SchemaMeta type', () => {
    const props: Record<string, SchemaProp> = {
      token: { type: 'string', 'x-meta': { sensitive: true } },
    }
    render(<AppConfigFields appName="secret-app" props={props} cur={{}} set={() => {}} />)
    expect((document.getElementById('app-cfg-secret-app-token') as HTMLInputElement).type)
      .toBe('password')
  })
})
