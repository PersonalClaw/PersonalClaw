import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { api } from '../../lib/api'
import { resetDataStore } from '../../lib/data'

// ── Settings › Secrets opens on the project its page named (PROJ PR 4, §5.10, §6.1) ─────────────
//
// A project's page's About › Secrets is "Secrets for Garden", which opens
// `#/settings/secrets?project=<id>`: the add form starts on that project, so a secret added there is
// read by that project's work alone. With no `project` it starts on every project, as before.

beforeEach(() => { resetDataStore() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDataStore() })

function mount(project?: string) {
  vi.spyOn(api, 'secrets').mockResolvedValue({
    secrets: [], counts: { total: 0, global: 0, project: 0, host: 0 }, empty_hint: 'No secrets stored yet.',
    store: { backend: 'dotenv', keychain_namespace: '', keychain_scope: '' },
  } as unknown as Awaited<ReturnType<typeof api.secrets>>)
  vi.spyOn(api, 'projects').mockResolvedValue([{ id: 'p-garden', name: 'Garden', status: 'active' }])
  vi.spyOn(api, 'projectSettings').mockResolvedValue({ default_project_id: '', pinned_project_ids: [] })
  return import('./SecretsPanel').then(({ SecretsPanel }) => render(<SecretsPanel project={project} />))
}

describe('Settings › Secrets from a project page', () => {
  it('starts the add form on the project the address names', async () => {
    await mount('p-garden')
    expect(await screen.findByRole('button', { name: /Garden/ })).toBeTruthy()
  })

  it('starts on every project with none named', async () => {
    await mount()
    expect(await screen.findByRole('button', { name: /Every project/ })).toBeTruthy()
  })
})
