import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor } from '@testing-library/react'
import type { ComposerValue } from '../../../ui/composer/types'

// ── The chat header's controller ────────────────────────────────────────────────────────────────
//
// `useChatHeader` keeps what the bar above an open chat does: the bound project's name for its
// context line, the inline rename and the title regenerate, and the cluster's actions. Driven here
// against a stream and a composer it is handed, the way ChatSession hands it them.

const SESSION = 'chat-8-header'

const h = vi.hoisted(() => ({
  project: vi.fn(), renameSession: vi.fn(), generateTitle: vi.fn(), channels: vi.fn(), handoffSession: vi.fn(),
  createSessionTemplate: vi.fn(), setSessionWorkspaceDir: vi.fn(),
  notify: vi.fn(), confirm: vi.fn(), promptInput: vi.fn(), copyText: vi.fn(),
}))
vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api, project: h.project, renameSession: h.renameSession, generateTitle: h.generateTitle, channels: h.channels,
      handoffSession: h.handoffSession, createSessionTemplate: h.createSessionTemplate, setSessionWorkspaceDir: h.setSessionWorkspaceDir,
    },
  }
})
vi.mock('../../../app/appSdk', () => ({ notify: h.notify }))
vi.mock('../../../ui/dialog', () => ({ confirm: h.confirm, promptInput: h.promptInput }))
vi.mock('../../../app/clipboard', () => ({ copyText: h.copyText }))

let useChatHeader: typeof import('./useChatHeader')['useChatHeader']

const SELECTION: ComposerValue = { agent: 'researcher', model: 'Auto', approval: 'normal', taskMode: 'agent', reasoning: 'high' }

/** The page around the controller: the stream's title and directory, the composer's selection. */
function mount({ projectId = '' } = {}) {
  const s = {
    setTitle: vi.fn(), setTitleNeed: vi.fn(), setAutoChain: vi.fn(), setWorkingDir: vi.fn(), setToast: vi.fn(),
  }
  const hook = renderHook(() => useChatHeader({
    stream: { sessionRef: { current: SESSION }, title: 'Feed refetch', setTitle: s.setTitle, setTitleNeed: s.setTitleNeed, setAutoChain: s.setAutoChain, workingDir: '', setWorkingDir: s.setWorkingDir },
    composer: { projectId, selection: SELECTION, setToast: s.setToast },
  }))
  return { ...hook, ...s }
}

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.channels.mockResolvedValue([])
  h.renameSession.mockResolvedValue({})
  h.confirm.mockResolvedValue(true)
  h.copyText.mockResolvedValue(true)
  ;({ useChatHeader } = await import('./useChatHeader'))
})
afterEach(() => cleanup())

describe('useChatHeader names the chat', () => {
  it("reads the bound project's name for the context line", async () => {
    h.project.mockResolvedValue({ name: 'Feed work' })
    const { result } = mount({ projectId: 'p-1' })
    await waitFor(() => expect(result.current.projectName).toBe('Feed work'))
    expect(h.project).toHaveBeenCalledWith('p-1')
  })

  it('renames inline: the title moves at once and the rename is written', async () => {
    const m = mount()
    act(() => m.result.current.beginRename())
    expect(m.result.current.renaming).toBe(true)
    expect(m.result.current.renameVal).toBe('Feed refetch')
    act(() => m.result.current.setRenameVal('  Feed fix  '))
    await act(async () => { await m.result.current.commitRename() })
    expect(m.result.current.renaming).toBe(false)
    expect(m.setTitle).toHaveBeenCalledWith('Feed fix')
    expect(m.setTitleNeed).toHaveBeenCalledWith('')
    expect(h.renameSession).toHaveBeenCalledWith(SESSION, 'Feed fix')
  })

  it('leaves the chat as it was for a blank or unchanged name', async () => {
    const m = mount()
    act(() => m.result.current.beginRename())
    await act(async () => { await m.result.current.commitRename() })
    expect(h.renameSession).not.toHaveBeenCalled()
    expect(m.setTitle).not.toHaveBeenCalled()
  })

  it('regenerates the title, and says what titles wait on when no model is chosen', async () => {
    h.generateTitle.mockResolvedValueOnce({ title: 'Validators ignored' })
    const m = mount()
    await act(async () => { await m.result.current.regenTitle() })
    expect(m.setTitle).toHaveBeenCalledWith('Validators ignored')
    h.generateTitle.mockResolvedValueOnce({ needs_model: 'Titles wait on a chores model. Choose one in Settings → Models.' })
    await act(async () => { await m.result.current.regenTitle() })
    expect(h.notify).toHaveBeenCalledWith('Titles wait on a chores model. Choose one in Settings → Models.', 'info')
    expect(m.result.current.regenningTitle).toBe(false)
  })
})

describe("useChatHeader runs the cluster's actions", () => {
  it('copies the chat link, and claims nothing when the copy fails', async () => {
    const m = mount()
    await act(async () => { await m.result.current.copyLink() })
    expect(h.copyText).toHaveBeenCalledWith(expect.stringContaining(`#/chat/${SESSION}`), 'the chat link')
    expect(h.notify).toHaveBeenCalledWith('Chat link copied.', 'success')
    h.notify.mockReset(); h.copyText.mockResolvedValue(false)
    await act(async () => { await m.result.current.copyLink() })
    expect(h.notify).not.toHaveBeenCalled()
  })

  it('continues on a channel once asked, and says where to pair an owner when it has none', async () => {
    h.channels.mockResolvedValue([
      { name: 'slack', display_name: 'Slack', owner: { id: 'U1' } },
      { name: 'telegram', display_name: 'Telegram', owner: {} },
      { name: 'webui', display_name: 'Web UI' },
    ])
    h.handoffSession.mockResolvedValue({})
    const m = mount()
    await waitFor(() => expect(m.result.current.handoffChannels.map((c) => c.name)).toEqual(['slack', 'telegram']))
    const [slack, telegram] = m.result.current.handoffChannels
    await act(async () => { await m.result.current.handOff(telegram) })
    expect(h.notify).toHaveBeenCalledWith(expect.stringContaining('Pair its owner in Settings → Providers → Telegram'), 'error')
    expect(h.confirm).not.toHaveBeenCalled()
    await act(async () => { await m.result.current.handOff(slack) })
    expect(h.confirm).toHaveBeenCalledWith(expect.objectContaining({ title: 'Continue on Slack?' }))
    expect(h.handoffSession).toHaveBeenCalledWith(SESSION, 'slack')
    expect(h.notify).toHaveBeenCalledWith('This chat is in your Slack messages now.', 'success')
  })

  it('saves the setup as a starter, never the messages', async () => {
    h.promptInput.mockResolvedValue('Deep dive')
    h.createSessionTemplate.mockResolvedValue({})
    const m = mount()
    await act(async () => { await m.result.current.saveAsTemplate() })
    expect(h.createSessionTemplate).toHaveBeenCalledWith({ name: 'Deep dive', agent: 'researcher', model: '', reasoning_effort: 'high', first_prompt: '' })
    expect(h.notify).toHaveBeenCalledWith(expect.stringContaining('Saved "Deep dive"'), 'success')
  })

  it('sets the working directory and takes the chain the gateway answers with', async () => {
    h.promptInput.mockResolvedValue(' /repo/feed ')
    h.setSessionWorkspaceDir.mockResolvedValue({ workspace_dir: '/repo/feed', auto_chain: 'local-first' })
    const m = mount()
    await act(async () => { await m.result.current.setWorkspaceDir() })
    expect(h.setSessionWorkspaceDir).toHaveBeenCalledWith(SESSION, '/repo/feed')
    expect(m.setWorkingDir).toHaveBeenCalledWith('/repo/feed')
    expect(m.setAutoChain).toHaveBeenCalledWith('local-first')
    expect(m.setToast).toHaveBeenCalled()
  })
})
