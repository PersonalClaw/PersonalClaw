import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import { mountPage, PID, project } from '../../test/projectPageHarness'

// ── The folder part says what automations may do there (§5.10, §5.13) ───────────────────────────
//
// Preview and unknown read the Preview line with Trust this folder (44 px at 390); trusted reads the
// trusted line with Stop trusting; missing and unreadable say different things; no folder says chats
// here use the usual workspace.

beforeEach(() => { resetDataStore() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDataStore() })

const DIR = '~/code/garden'
const part = async () => (await screen.findByRole('heading', { level: 3, name: 'Folder' })).closest('section')!

describe("a project's folder", () => {
  it.each(['preview', 'unknown'] as const)('%s: automations only read here, until you trust it', async (trust) => {
    mountPage({ project: project(PID, 'Garden', { workspace_dir: DIR, folder: { resolved: '/x/garden', trust } }) })
    const p = await part()
    await waitFor(() => expect(p.textContent).toContain('Automations here can only read, until you trust this folder.'))
    const trustIt = within(p).getByRole('button', { name: 'Trust this folder' })
    expect(trustIt.className).toMatch(/max-lg:min-h-11|min-h-11/)
  })

  it('trusted: automations may change files and run commands, and Stop trusting', async () => {
    mountPage({ project: project(PID, 'Garden', { workspace_dir: DIR, folder: { resolved: '/x/garden', trust: 'trusted' } }) })
    const p = await part()
    await waitFor(() => expect(p.textContent).toContain('Automations here may change files and run commands.'))
    expect(within(p).getByRole('button', { name: 'Stop trusting' })).toBeTruthy()
    expect(within(p).queryByRole('button', { name: 'Trust this folder' })).toBeNull()
  })

  it('missing and unreadable say different things', async () => {
    mountPage({ project: project(PID, 'Garden', { workspace_dir: DIR, folder: { resolved: '/x/garden', trust: 'preview' } }), folder: 404 })
    const p = await part()
    await waitFor(() => expect(p.textContent).toContain('This folder is missing.'))
    cleanup(); resetDataStore(); vi.restoreAllMocks()
    mountPage({ project: project(PID, 'Garden', { workspace_dir: DIR, folder: { resolved: '/x/garden', trust: 'preview' } }), folder: 500 })
    const q = await part()
    await waitFor(() => expect(q.textContent).toContain("PersonalClaw can't read this folder right now. It may still be there."))
    expect(q.textContent).not.toContain('missing')
  })

  it('none: chats here use your usual workspace', async () => {
    mountPage({ project: project(PID, 'Garden', { workspace_dir: '', folder: null }) })
    const p = await part()
    expect(within(p).getByText('No folder. Chats here use your usual workspace.')).toBeTruthy()
    expect(within(p).getByRole('button', { name: 'Choose a folder' })).toBeTruthy()
  })
})
