import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react'
import { useState } from 'react'

// ── Exporting your agents to Claude Code, from the Agents page ───────────────────────────────────
//
// The outbound export had every rail in the gateway and no way in: no page, no command and no route
// reached it. These pin the way in — the Agents header opens it — and what it has to do there:
//
//   · show the folder Claude Code reads its agents from (the gateway's answer, which follows
//     CLAUDE_CONFIG_DIR) and confirm THAT folder, never one the page made up;
//   · offer only your own agents — not the built-ins that run the platform, not the default agent;
//   · refuse the whole export while a file PersonalClaw did not write is in the way, in the
//     gateway's words, and let unticking that agent export the rest;
//   · say a refused or failed write inside the dialog, and read the plan again.

const DEST = '/home/you/.claude/agents'
const catalog = {
  agents: [
    { name: 'PersonalClaw', revision: 'r0' },
    { name: 'personalclaw-lite', reserved: true, revision: 'r1' },
    { name: 'code-reviewer', description: 'Reviews diffs', revision: 'r2' },
    { name: 'talk-editor', description: 'Edits talks', revision: 'r3' },
  ],
  default_agent: 'PersonalClaw',
}
const REFUSAL = `Refusing to overwrite ${DEST}/code-reviewer.md (not written by PersonalClaw). Nothing is exported while it is there: leave it out to export the rest.`

/** What the gateway answers: her own `code-reviewer.md` is already in the folder. */
function planFor(names: string[]) {
  return {
    ok: true,
    format: 'claude-code-agents',
    dest: DEST,
    files: names.map((n) => ({ path: `${n}.md`, state: n === 'code-reviewer' ? 'theirs' : 'new', entities: [n] })),
    blocked: [],
    refusal: names.includes('code-reviewer') ? REFUSAL : null,
  }
}

let preview = vi.fn()
let exportAgents = vi.fn()
let notify = vi.fn()

function mockApi() {
  vi.doMock('../../app/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (...a: unknown[]) => notify(...a),
  }))
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      agents: () => Promise.resolve(catalog),
      agentProviders: () => Promise.resolve([]),
      previewAgentExport: (names: string[]) => preview(names),
      exportAgents: (names: string[], dest: string) => exportAgents(names, dest),
    },
  }))
}

async function mount() {
  const { AgentsListPage } = await import('./AgentsListPage')
  function Harness() {
    const [query, setQueryState] = useState<Record<string, string>>({})
    const setQuery = (patch: Record<string, string | null | undefined>) => setQueryState((q) => {
      const next = { ...q }
      for (const [k, v] of Object.entries(patch)) { if (v == null) delete next[k]; else next[k] = v }
      return next
    })
    return <AgentsListPage query={query} setQuery={setQuery} onCreate={() => {}} />
  }
  render(<Harness />)
}

async function openExport() {
  fireEvent.click(await screen.findByRole('button', { name: 'Export to Claude Code' }))
  return screen.findByRole('dialog', { name: 'Export to Claude Code' })
}

async function leaveOutHerReviewer(dialog: HTMLElement) {
  await within(dialog).findByText(REFUSAL)
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Export code-reviewer' }))
  await waitFor(() => expect(preview).toHaveBeenLastCalledWith(['talk-editor']))
  const ready = await within(dialog).findByRole('button', { name: 'Export 1 agent' })
  await waitFor(() => expect(ready).not.toHaveAttribute('aria-disabled'))
  return ready
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  preview = vi.fn((names: string[]) => Promise.resolve(planFor(names)))
  exportAgents = vi.fn((names: string[]) => Promise.resolve({
    ok: true, dest: DEST, written: names.map((n) => `${DEST}/${n}.md`), unchanged: [],
    message: `Exported ${names.length} agent to ${DEST}: ${names.map((n) => `${n}.md`).join(', ')}.`,
  }))
  notify = vi.fn()
})

afterEach(() => { vi.restoreAllMocks() })

describe('the Agents page exports your agents to Claude Code', () => {
  it('opens from the header, offering only your own agents, and shows the folder the gateway named', async () => {
    mockApi()
    await mount()
    const dialog = await openExport()
    await waitFor(() => expect(preview).toHaveBeenCalledWith(['code-reviewer', 'talk-editor']))
    expect(within(dialog).queryByRole('checkbox', { name: 'Export PersonalClaw' }), 'the default agent stays').toBeNull()
    expect(within(dialog).queryByRole('checkbox', { name: 'Export personalclaw-lite' }), 'a built-in stays').toBeNull()
    expect(await within(dialog).findByText(DEST)).toBeInTheDocument()
    expect(within(dialog).getByText(/follows CLAUDE_CONFIG_DIR/)).toBeInTheDocument()
  })

  it('refuses the whole export while her own file is in the way, and unticking it exports the rest', async () => {
    mockApi()
    await mount()
    const dialog = await openExport()
    // The gateway's sentence, and the row that says which file.
    expect(await within(dialog).findByText(REFUSAL)).toBeInTheDocument()
    expect(within(dialog).getByText('A file PersonalClaw did not write is there. It is never overwritten.')).toBeInTheDocument()
    const blocked = within(dialog).getByRole('button', { name: 'Export 2 agents' })
    expect(blocked).toHaveAttribute('aria-disabled', 'true')
    expect(blocked.getAttribute('title')).toContain(REFUSAL)
    fireEvent.click(blocked)
    expect(exportAgents, 'a refused plan is never written').not.toHaveBeenCalled()

    fireEvent.click(await leaveOutHerReviewer(dialog))
    // The confirmation names the folder the plan showed.
    await waitFor(() => expect(exportAgents).toHaveBeenCalledWith(['talk-editor'], DEST))
    await waitFor(() => expect(notify).toHaveBeenCalledWith(`Exported 1 agent to ${DEST}: talk-editor.md.`, 'success'))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('says a refused write inside the dialog, and reads the plan again', async () => {
    const moved = `This export was not confirmed for /home/you/other/agents, the folder Claude Code reads its agents from now, so nothing was written. Review the export and confirm again.`
    exportAgents = vi.fn(() => Promise.reject(new Error(moved)))
    mockApi()
    await mount()
    const dialog = await openExport()
    fireEvent.click(await leaveOutHerReviewer(dialog))
    const said = await within(dialog).findByText(moved)
    expect(said.closest('[role="alert"]'), 'announced, beside the button that failed').not.toBeNull()
    // Opened, unticked, and once more after the refusal: the list is the folder as it is now.
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(3))
    expect(notify).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: 'Export to Claude Code' })).toBeInTheDocument()
  })

  it('says a check that failed, offers it again, and writes nothing', async () => {
    preview = vi.fn(() => Promise.reject(new Error('gateway down')))
    mockApi()
    await mount()
    const dialog = await openExport()
    expect(await within(dialog).findByText("Couldn't check the export: gateway down. Nothing was written.")).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Export 2 agents' })).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(2))
    expect(exportAgents).not.toHaveBeenCalled()
  })
})

describe('with no agents of your own', () => {
  it('says so, asks the gateway nothing, and offers nothing to press', async () => {
    mockApi()
    const { ExportAgentsDialog } = await import('./ExportAgentsDialog')
    render(<ExportAgentsDialog agents={[]} onClose={() => {}} />)
    expect(screen.getByText(/You have no agents of your own to export yet/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Export/ })).toBeNull()
    expect(preview).not.toHaveBeenCalled()
  })
})
