// @module-tag tree-scan
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useRef, useState } from 'react'
import { join } from 'node:path'
import { ApiError, type FsEntry } from '../../../lib/api'
import type { RouteProps } from '../../../app/useQueryState'
import { CommandPalette, keymapCommands } from '../../../app/CommandPalette'
import { bindingLabel, useBoundRunner } from '../../../app/keymap'
import { readSource } from '../../../test/sourceTree'
import { userTurn, assistantTurn, type ChatTurn, type SubagentCard } from '../chatTypes'
import { useChatPanes } from './useChatPanes'
import { ChatPanes } from './ChatPanes'

// ── A chat's working folder, beside the conversation ────────────────────────────────────────────
//
// ⌘⇧E (the keymap's `workspace-tree` row) shows and hides the files of the folder the chat works
// in, docked beside the transcript like the chat's other panes and on the URL like them (`?tree`).
// A click on a file opens it in the chat's file peek. The tree lists through the Files page's own
// listing, so it shows only what that page may already show, and it only reads: no row renames,
// deletes or takes an upload. A chat with no working folder says so. The palette lists the key, and
// runs it while a chat is open.

const h = vi.hoisted(() => ({ fileList: vi.fn() }))
vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  return { ...real, api: { ...real.api, fileList: (p: string) => h.fileList(p) } }
})
// The peek is the Files page's whole editor; here it only has to say which file it was asked for.
vi.mock('../ChatFilePanel', () => ({
  ChatFilePanel: ({ path }: { path: string }) => <div role="region" aria-label="File peek">{path}</div>,
}))

const FOLDER = '/work/feed'
const LISTINGS: Record<string, FsEntry[]> = {
  [FOLDER]: [
    { name: 'src', path: `${FOLDER}/src`, is_dir: true },
    { name: 'README.md', path: `${FOLDER}/README.md`, is_dir: false },
  ],
  [`${FOLDER}/src`]: [{ name: 'fetch.py', path: `${FOLDER}/src/fetch.py`, is_dir: false }],
}
const TURNS: ChatTurn[] = [userTurn('Fix the feed'), assistantTurn('Edited `src/fetch.py`.')]
const TRANSCRIPT = {
  mapOpen: false, isMobile: false, setMapOpen: () => {}, sessionEntries: [],
  pendingMapJump: { current: null }, runPendingMapJump: () => {},
} as unknown as Parameters<typeof ChatPanes>[0]['transcript']

/** The address the chat last wrote. */
const seen: { query: Record<string, string> } = { query: {} }

/** A started chat as `ChatSession` composes it: its panes' controller and their view, over a URL. */
function Chat({ workingDir, streaming = false, initial = {}, focus = false }: {
  workingDir: string; streaming?: boolean; initial?: Record<string, string>; focus?: boolean
}) {
  const [query, setQ] = useState<Record<string, string>>(initial)
  seen.query = query
  const setQuery: RouteProps['setQuery'] = (patch) => setQ((prev) => {
    const next = { ...prev }
    for (const [k, v] of Object.entries(patch)) { if (v == null) delete next[k]; else next[k] = v }
    return next
  })
  const [subagents, setSubagents] = useState<SubagentCard[]>([])
  const [sideMsgs, setSideMsgs] = useState<{ q: string; a: string; runId: string; done: boolean }[]>([])
  const [sideBusy, setSideBusy] = useState(false)
  const sideOpenedRef = useRef(false)
  const panes = useChatPanes({
    query, setQuery, focus,
    stream: { sessionRef: { current: 'chat-feed' }, turns: TURNS, setSubagents, sideBusy, setSideBusy, setSideMsgs, sideOpenedRef },
  })
  return (
    <ChatPanes panes={panes} stream={{ subagents, sideMsgs, sideBusy, workingDir, streaming, sessionRef: { current: 'chat-feed' } }} transcript={TRANSCRIPT}
      started setQuery={setQuery} navigate={() => {}} send={() => {}} />
  )
}

const chord = () => new KeyboardEvent('keydown', { key: 'E', code: 'KeyE', metaKey: true, shiftKey: true, cancelable: true })
const pressTreeKey = () => { const e = chord(); act(() => { window.dispatchEvent(e) }); return e }
const pane = () => screen.findByRole('region', { name: 'Working folder' })

beforeEach(() => {
  sessionStorage.clear()
  h.fileList.mockReset()
  h.fileList.mockImplementation(async (p: string) => ({ entries: LISTINGS[p] ?? [] }))
})
afterEach(cleanup)

describe('⌘⇧E shows the folder a chat works in', () => {
  it('shows it beside the conversation, and hides it again', async () => {
    render(<Chat workingDir={FOLDER} />)
    expect(screen.queryByRole('region', { name: 'Working folder' })).toBeNull()
    expect(pressTreeKey().defaultPrevented).toBe(true)
    const tree = await pane()
    expect(within(tree).getByText(FOLDER)).toBeTruthy()
    await within(tree).findByRole('button', { name: 'README.md' })
    expect(h.fileList).toHaveBeenCalledWith(FOLDER)
    expect(seen.query.tree).toBe('1')
    pressTreeKey()
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Working folder' })).toBeNull())
    expect(seen.query.tree).toBeUndefined()
  })

  it('opens a file in the peek on a click, and a folder in place', async () => {
    render(<Chat workingDir={FOLDER} initial={{ tree: '1' }} />)
    const tree = await pane()
    fireEvent.click(await within(tree).findByRole('button', { name: 'src' }))
    fireEvent.click(await within(tree).findByRole('button', { name: 'fetch.py' }))
    expect((await screen.findByRole('region', { name: 'File peek' })).textContent).toBe(`${FOLDER}/src/fetch.py`)
    expect(seen.query.file).toBe(`${FOLDER}/src/fetch.py`)
    // The tree stays, and marks the file the peek shows.
    expect(within(tree).getByRole('button', { name: 'fetch.py' }).getAttribute('aria-current')).toBe('page')
  })

  it('says so in a chat with no working folder, and lists nothing', async () => {
    render(<Chat workingDir="" />)
    pressTreeKey()
    const tree = await pane()
    expect(tree.textContent).toContain('This chat has no working folder.')
    expect(h.fileList).not.toHaveBeenCalled()
  })

  it('lists only what the Files page may: a folder it may not browse is refused here too, with why', async () => {
    h.fileList.mockRejectedValue(new ApiError('invalid or forbidden path', 400))
    render(<Chat workingDir="/private/elsewhere" initial={{ tree: '1' }} />)
    const tree = await pane()
    expect((await within(tree).findByRole('alert')).textContent).toMatch(/outside the folders PersonalClaw can browse/)
    expect(h.fileList).toHaveBeenCalledWith('/private/elsewhere')
  })

  it('only reads: no row renames, deletes or takes an upload', async () => {
    render(<Chat workingDir={FOLDER} initial={{ tree: '1' }} />)
    const tree = await pane()
    const row = await within(tree).findByRole('button', { name: 'README.md' })
    expect(within(tree).queryAllByRole('button', { name: /^Actions for/ })).toEqual([])
    fireEvent.contextMenu(row)
    expect(screen.queryByRole('menu')).toBeNull()
    expect(tree.querySelector('input[type="file"]')).toBeNull()
  })

  it('lists the folder again when a turn ends, since the agent may have written there', async () => {
    const { rerender } = render(<Chat workingDir={FOLDER} streaming initial={{ tree: '1' }} />)
    const tree = await pane()
    await within(tree).findByRole('button', { name: 'README.md' })
    h.fileList.mockImplementation(async (p: string) => ({
      entries: p === FOLDER ? [...LISTINGS[FOLDER], { name: 'NOTES.md', path: `${FOLDER}/NOTES.md`, is_dir: false }] : LISTINGS[p] ?? [],
    }))
    rerender(<Chat workingDir={FOLDER} streaming={false} initial={{ tree: '1' }} />)
    expect(await within(tree).findByRole('button', { name: 'NOTES.md' })).toBeTruthy()
  })

  it('is a docked pane in focus mode: entering it closes the tree, and leaving it brings it back', async () => {
    const { rerender } = render(<Chat workingDir={FOLDER} initial={{ tree: '1', activity: '1' }} />)
    await pane()
    rerender(<Chat workingDir={FOLDER} initial={{}} focus />)
    expect(seen.query.tree).toBeUndefined()
    expect(seen.query.activity).toBeUndefined()
    rerender(<Chat workingDir={FOLDER} initial={{}} />)
    expect(seen.query.tree).toBe('1')
    // In it, asking for the tree opens the tree alone.
    rerender(<Chat workingDir={FOLDER} initial={{}} focus />)
    pressTreeKey()
    expect(seen.query).toEqual({ tree: '1' })
  })
})

/** The shell's palette, given the runner the chat bound, as `App` gives it. */
function Shell({ chat }: { chat: boolean }) {
  const tree = useBoundRunner('workspace-tree')
  return (
    <>
      <CommandPalette commands={keymapCommands({ 'workspace-tree': tree })} navigate={() => {}} />
      {chat && <Chat workingDir={FOLDER} />}
    </>
  )
}

const openPalette = () => act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', code: 'KeyK', metaKey: true, cancelable: true })) })
const treeRow = () => screen.getByRole('option', { name: /Show or hide the chat's working folder/ })

describe('the palette lists ⌘⇧E', () => {
  it('dimmed with why while no chat is open, and the key is left to the browser', () => {
    render(<Shell chat={false} />)
    expect(pressTreeKey().defaultPrevented).toBe(false)
    openPalette()
    expect(treeRow().textContent).toContain(bindingLabel('workspace-tree'))
    expect(treeRow().getAttribute('aria-disabled')).toBe('true')
    expect(treeRow().textContent).toContain('Open a chat to see its working folder')
  })

  it("runs it while a chat is open: the chat's runner is the palette's", async () => {
    render(<Shell chat />)
    openPalette()
    expect(treeRow().getAttribute('aria-disabled')).not.toBe('true')
    fireEvent.click(treeRow())
    expect(await pane()).toBeTruthy()
    expect(seen.query.tree).toBe('1')
  })

  it("is handed to the shell's palette by App, from the runner the chat bound", () => {
    const app = readSource(join(process.cwd(), 'src', 'app', 'App.tsx'))
    expect(app).toMatch(/const chatTree = useBoundRunner\('workspace-tree'\)/)
    expect(app.slice(app.indexOf('const keyRunners'), app.indexOf('useKeyBinding(\'sidebar\''))).toMatch(/'workspace-tree': chatTree/)
  })
})
