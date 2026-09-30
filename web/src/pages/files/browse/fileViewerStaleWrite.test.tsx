import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { SaveAsArtifact } from './FileViewer'

// ── A file saved from a stale copy is refused, and the change is kept ─────────────────────────
//
// The editor saves the WHOLE file, built from the copy it read. The agent's `edit_file`, an
// artifact's write-through or another tab could rewrite the file while it was open, and the old
// viewer's save sent its copy anyway — behind nothing but an "Overwrite?" prompt that only
// appeared if the 1-second watch had noticed in time. The gateway now refuses a copy that went
// stale (`409 stale_write`); this pins what the viewer does with that refusal, for its own save
// and for "Save as artifact", whose body is the same draft written through to the same file.
//
// 🪤 Driven at ContentSurface's prop seam: Monaco does not mount under jsdom (no web workers —
// `pages/artifacts/artifactDraftSurvivesNavigation.test.tsx` documents the same trap). The
// surface's own draft/dirty behaviour is its own suite's; what is under test here is the base
// the viewer sends, what it shows on a refusal, and what it asks the surface to hold.

const PATH = '/ws/notes.py'
// Two edits far enough apart to re-apply cleanly: the user changes line 1, the agent line 4.
const READ = 'a\nb\nc\nd\n'
const AGENTS = 'a\nb\nc\nD\n'
const MINE = 'A\nb\nc\nd\n'
const BOTH = 'A\nb\nc\nD\n'

function staleWrite() {
  return Object.assign(new Error(`This write replaces the file '${PATH}', which changed…`), { status: 409, code: 'stale_write' })
}

/** The props the viewer handed ContentSurface on its latest render, and what it asked it to hold. */
let seen: Record<string, any> = {}
const replaced: string[] = []

async function mount({ acceptFirst = false } = {}) {
  vi.resetModules()
  seen = {}
  replaced.length = 0
  // What is on disk: the first read is the copy the page paints; every later read sees the agent's
  // edit, which landed while the page was open.
  let reads = 0
  const fileRead = vi.fn(async () => {
    reads += 1
    return reads === 1 || acceptFirst
      ? { content: READ, truncated: false, binary: false, revision: 'r1' }
      : { content: AGENTS, truncated: false, binary: false, revision: 'r2' }
  })
  const fileWrite = vi.fn(async (_p: string, _c: string, base: string) =>
    base === 'r2' || acceptFirst ? { ok: true, revision: 'r3' } : Promise.reject(staleWrite()))
  vi.doMock('../../../lib/api', async (orig) => {
    const real = await orig<typeof import('../../../lib/api')>()
    return {
      ...real,
      api: { ...real.api, fileRead, fileWrite, fileWatchUrl: () => '/watch', fileRawUrl: () => '/raw', revealPath: async () => ({}) },
    }
  })
  vi.doMock('../../../ui/content/ContentSurface', async () => {
    const React = await import('react')
    return {
      ContentSurface: React.forwardRef((props: Record<string, any>, ref) => {
        React.useImperativeHandle(ref, () => ({ save: () => {}, replaceDraft: (t: string) => { replaced.push(t) } }))
        seen = props
        return <div data-testid="surface">{props.headerExtras}{props.banner}</div>
      }),
    }
  })
  // The content-type registry is module state, reset with the modules above.
  const { registerBuiltinContentTypes } = await import('../../../ui/content/registerBuiltins')
  registerBuiltinContentTypes()
  const { FileViewer } = await import('./FileViewer')
  const onSaved = vi.fn()
  let saveAsArtifact: SaveAsArtifact | null = null
  await act(async () => {
    render(<FileViewer entry={{ name: 'notes.py', path: PATH, is_dir: false }} onSaved={onSaved}
      onSaveAsArtifact={(_e, save) => { if (typeof save === 'function') saveAsArtifact = save }} />)
  })
  await waitFor(() => expect(seen.onSave).toBeTypeOf('function'))
  return { fileRead, fileWrite, onSaved, saveAsArtifact: () => saveAsArtifact }
}

describe('a file save from a stale copy', () => {
  it('is refused into the notice: nothing saved, the draft kept and held still', async () => {
    const { fileWrite, onSaved } = await mount()
    await act(async () => { await seen.onSave(MINE) })

    // The refused save named the revision of the copy the page painted.
    expect(fileWrite.mock.calls[0]).toEqual([PATH, MINE, 'r1'])
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/This file changed elsewhere/)
    expect(alert.textContent).toMatch(/your change\s+wasn’t saved/)
    expect(within(alert).getByRole('button', { name: 'Review the difference' })).toBeTruthy()
    // Kept: the viewer asked the surface to replace nothing, and holds the draft still so the
    // change the notice re-applies is the one on screen.
    expect(replaced).toEqual([])
    expect(seen.locked).toBe(true)
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('Reload and reapply saves the change on top of the agent’s edit, over the new revision', async () => {
    const { fileWrite, onSaved } = await mount()
    await act(async () => { await seen.onSave(MINE) })
    const reapply = within(await screen.findByRole('alert')).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })

    await waitFor(() => expect(fileWrite).toHaveBeenCalledTimes(2))
    expect(fileWrite.mock.calls[1]).toEqual([PATH, BOTH, 'r2'])
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    // What was stored is now the editor's text and its copy — not the pre-reapply draft.
    expect(replaced).toEqual([BOTH])
    expect(seen.content).toBe(BOTH)
    expect(seen.draftBase).toEqual({ value: BOTH, revision: 'r3' })
    expect(seen.locked).toBe(false)
    expect(onSaved).toHaveBeenCalledWith(BOTH)
  })

  it('Discard my change writes nothing more and puts the file’s current text back', async () => {
    const { fileWrite } = await mount()
    await act(async () => { await seen.onSave(MINE) })
    const alert = await screen.findByRole('alert')
    await act(async () => { fireEvent.click(within(alert).getByRole('button', { name: 'Discard my change' })) })

    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    expect(fileWrite).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(replaced).toEqual([AGENTS]))
    expect(seen.draftBase).toEqual({ value: AGENTS, revision: 'r2' })
  })

  it('a save that lands names the revision the page painted, and saves again from the answer', async () => {
    const { fileWrite, onSaved } = await mount({ acceptFirst: true })
    await act(async () => { await seen.onSave(MINE) })

    expect(fileWrite.mock.calls[0]).toEqual([PATH, MINE, 'r1'])
    expect(screen.queryByRole('alert')).toBeNull()
    expect(onSaved).toHaveBeenCalledWith(MINE)
    // The next save's base is the revision the write answered with, not the first read's.
    await waitFor(() => expect(seen.draftBase).toEqual({ value: MINE, revision: 'r3' }))
  })
})

describe('“Save as artifact” from a stale copy', () => {
  it('goes over the same base, and a refusal lands in the same notice with the draft kept', async () => {
    const { saveAsArtifact } = await mount()
    act(() => { seen.onDraftChange(MINE, true) })
    // The header's Artifact action hands the host the viewer's bound save.
    fireEvent.click(screen.getByRole('button', { name: /artifact/i }))
    const create = vi.fn(async () => { throw staleWrite() })

    let landed: boolean | undefined
    await act(async () => { landed = await saveAsArtifact()!(create) })

    expect(create.mock.calls[0]).toEqual([MINE, 'r1'])
    expect(landed).toBe(false)
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/This file changed elsewhere/)
    expect(replaced).toEqual([])
  })
})
