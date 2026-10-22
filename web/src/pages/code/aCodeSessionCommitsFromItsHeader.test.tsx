/** A code session commits its changes from its header once its run has ended (#3821).
 *
 *  The header offers Commit when the run has ended (its supervisor commits into the tree until
 *  then) and its workspace holds something uncommitted. Commit asks for a message in a dialog that
 *  says what it commits, that nothing is pushed and that the repository's hooks do not run here,
 *  and sends it to the session's commit route;
 *  the toast names the commit. Open PR is absent: no app opens a pull request yet. This renders the
 *  whole cockpit on a route held in memory.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

const h = vi.hoisted(() => {
  const WS = '/repo/feed'
  const state = { status: 'complete', statuses: { [`${WS}/src/a.py`]: ' M' } as Record<string, string> }
  const calls: { name: string; args: unknown[] }[] = []
  const reads: Record<string, (...a: unknown[]) => Promise<unknown>> = {
    uLoop: () => Promise.resolve({
      id: 'loop-1', name: 'Feed refetch fix', kind: 'code', status: state.status, workspace_dir: WS, files_dir: '/pc/loops/loop-1',
      plan: [{ stage: 'build', title: 'Build' }], phase_status: { build: 'done' }, task_list_ids: { build: 'l-1' },
      kind_config: { project_kind: 'brownfield' },
    }),
    allTasks: () => Promise.resolve({ tasks: [] }),
    agentProviders: () => Promise.resolve([]),
    approvals: () => Promise.resolve([]),
    fileList: () => Promise.resolve({ entries: [] }),
    fileGitStatus: () => Promise.resolve({ branch: 'main', statuses: state.statuses, repoRoot: WS }),
    fileGitLog: () => Promise.resolve({ repoRoot: WS, commits: [] }),
    fileRead: () => Promise.resolve({ content: '' }),
    fileGitOriginal: () => Promise.resolve({ content: '' }),
    loopCommit: () => Promise.resolve({ ok: true, sha: '0123abcd4567ef' }),
  }
  return { calls, reads, state }
})

vi.mock('../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/api')>()
  const api = new Proxy(real.api as unknown as Record<string, unknown>, {
    get: (_target, prop: string) => (...args: unknown[]) => {
      h.calls.push({ name: prop, args })
      return prop in h.reads ? h.reads[prop](...args) : Promise.resolve({})
    },
  })
  return { ...real, api }
})
vi.mock('../loops/useRunStream', () => ({ useRunStream: () => ({ connected: true }) }))
vi.mock('../../lib/useChatSocket', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/useChatSocket')>()
  return { ...real, useChatSocket: () => {} }
})
vi.mock('../../lib/useWorkspaceMissing', () => ({ useWorkspaceMissing: () => false }))
vi.mock('../../lib/useVisiblePoll', () => ({ useVisiblePoll: () => {} }))

const { CodeCockpitPage } = await import('./CodeCockpitPage')
const { DialogHost } = await import('../../ui/dialog/DialogHost')

function Cockpit() {
  return (
    <>
      <CodeCockpitPage id="loop-1" onBack={() => {}} onDeleted={() => {}} query={{}} setQuery={() => {}} />
      <DialogHost />
    </>
  )
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  h.calls.length = 0
  h.state.status = 'complete'
  h.state.statuses = { '/repo/feed/src/a.py': ' M' }
})
afterEach(cleanup)

const commitControl = () => screen.queryByRole('button', { name: 'Commit' })
const settled = async () => {
  await screen.findByText('Feed refetch fix')
  await waitFor(() => expect(h.calls.filter((c) => c.name === 'fileGitStatus').length).toBeGreaterThan(0))
}

describe("a code session's Commit", () => {
  it('commits with the message written, through the session commit route, and names the commit', async () => {
    render(<Cockpit />)
    const control = await screen.findByRole('button', { name: 'Commit' })
    const hooks = "Commits here don't run the repository's git hooks; commit from your terminal to run them."
    expect(control.getAttribute('title'), 'the control says the hooks do not run here').toContain(hooks)
    fireEvent.click(control)
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toMatch(/Nothing is pushed/)
    expect(dialog.textContent).toContain(hooks)
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Message' }), { target: { value: '  Retry the refetch  ' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Commit' }))
    await waitFor(() => expect(h.calls.filter((c) => c.name === 'loopCommit').map((c) => c.args)).toEqual([['loop-1', 'Retry the refetch']]))
    expect(await screen.findByText('Committed 0123abc')).toBeInTheDocument()
  })

  it('sends nothing until a message is written', async () => {
    render(<Cockpit />)
    fireEvent.click(await screen.findByRole('button', { name: 'Commit' }))
    const dialog = await screen.findByRole('dialog')
    const send = within(dialog).getByRole('button', { name: 'Commit' })
    expect(send).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(send)
    expect(h.calls.map((c) => c.name)).not.toContain('loopCommit')
    // The dialog store outlives a render: leave it closed for the next case.
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it.each(['running', 'needs_input', 'paused'])('is not offered while the run is %s', async (status) => {
    h.state.status = status
    render(<Cockpit />)
    await settled()
    expect(commitControl()).toBeNull()
  })

  it('is not offered with nothing uncommitted, and Open PR is never offered', async () => {
    h.state.statuses = {}
    render(<Cockpit />)
    await settled()
    expect(commitControl()).toBeNull()
    expect(screen.queryByRole('button', { name: /pull request|Open PR/i })).toBeNull()
  })
})
