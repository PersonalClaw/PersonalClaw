/** Issue 311: typing a path then clicking "Use this folder" bound the PREVIOUSLY-BROWSED directory.
 *
 * The picker kept two values — `path` (the dir being browsed) and `pathDraft` (what is in the input)
 * — the footer button submitted `path`, and the input reverted the draft on blur. Clicking the
 * button blurs the input first, so the typed value was discarded a moment before the click handler
 * ran. Nothing was shown: the field displayed the typed path at the moment of the click, the dialog
 * closed as if it had worked, and the PUT carried the browsed directory.
 *
 * 🔑 WHY THIS IS THE WORST POSSIBLE THING TO GET WRONG SILENTLY. What gets bound is `workspace_dir`
 * — the tree that loops and code sessions read, write and run `bash` in. The browse location
 * defaults to the user's HOME, so the most likely accidental outcome measured in the report was
 * binding a project's workspace to the whole of `/Users/<user>`. Three different typed paths were
 * tried; all three bound `/Users/me`.
 *
 * So a typed path must be BOUND or REFUSED OUT LOUD, never substituted. `browse-dirs` is what makes
 * that possible without new backend surface: it 404s a path that does not exist and returns the
 * REALPATH of one that does — which is also the form the cockpit's tree, git status and
 * follow-the-worker all emit, and the same reason `createFolder` binds the resolved path rather
 * than the string it sent.
 *
 * The fake below maps `/tmp` → `/private/tmp` for exactly that reason: on macOS the two are the
 * same directory under different names, and binding the unresolved one reintroduces the mismatch
 * the cockpit then has to paper over.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ApiError } from '../../lib/api'

const HOME = '/Users/me'

/** A minimal stand-in for the browse-dirs route: realpath-resolving, 404 on a missing dir. */
const TREE: Record<string, { parent: string; dirs: string[]; in_repo?: boolean }> = {
  [HOME]: { parent: '/Users', dirs: ['Projects', 'Documents'] },
  '/private/tmp': { parent: '/private', dirs: [] },
}
const realpath = (p: string) => (p === '/tmp' ? '/private/tmp' : p.replace(/\/+$/, ''))

const browseDirs = vi.fn(async (raw?: string) => {
  const at = raw ? realpath(raw) : HOME
  const node = TREE[at]
  // Mirrors the route: a path that is not a browsable directory is a 404 whose message the
  // picker surfaces verbatim.
  if (!node) throw new ApiError('No such directory', 404)
  return {
    path: at,
    parent: node.parent,
    in_repo: !!node.in_repo,
    dirs: node.dirs.map((n) => ({ name: n, path: `${at}/${n}` })),
  }
})

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return { ...actual, api: { ...actual.api, browseDirs: (p?: string) => browseDirs(p) } }
})

// Imported after the mock registration so the picker binds the stubbed route.
const { WorkspacePicker } = await import('./WorkspacePicker')

/** Render the picker in the shape the project-workspace bind uses (brownfield + create allowed). */
function renderPicker() {
  const onPick = vi.fn()
  render(<WorkspacePicker mode="brownfield" allowCreate onPick={onPick} onClose={() => {}} />)
  return onPick
}

const pathBar = () => screen.getByLabelText('Workspace path')
const useButton = () => screen.getByRole('button', { name: /Use this folder/ })
/** `getAllBy…`: the footer caption and its child span both carry this text, and asserting on the
 *  ancestor chain would be asserting on the markup rather than on what the user can read. */
const readsInFooter = (re: RegExp) => screen.queryAllByText(re).length > 0

/** The picker has finished its initial browse and is showing the default location. */
async function loaded() {
  await waitFor(() => expect(pathBar()).toHaveValue(HOME))
}

/** Replace the path bar's contents, as a user selecting-all and typing over it would. */
async function typePath(p: string) {
  await userEvent.clear(pathBar())
  await userEvent.type(pathBar(), p)
}

beforeEach(() => { browseDirs.mockClear() })

describe('typing a path and clicking "Use this folder"', () => {
  it('binds the typed path, not the directory being browsed', async () => {
    const onPick = renderPicker()
    await loaded()

    await typePath('/tmp')
    expect(pathBar()).toHaveValue('/tmp')

    // 🪤 The click itself BLURS the input — which is what used to discard the draft. Driving it
    // with a real click (not a synthetic submit) is the whole point of this assertion.
    await userEvent.click(useButton())

    // Resolved, because /tmp and /private/tmp are one directory and the rest of the cockpit
    // speaks the resolved form.
    await waitFor(() => expect(onPick).toHaveBeenCalledWith('/private/tmp'))
    expect(onPick).not.toHaveBeenCalledWith(HOME)
  })

  it('refuses a path that does not exist, out loud, instead of binding somewhere else', async () => {
    const onPick = renderPicker()
    await loaded()

    await typePath('/definitely/not/here/xyz')
    await userEvent.click(useButton())

    // The refusal is visible and says why…
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/No such directory/i))
    // …and nothing was bound. A silent wrong bind is worse than an error, so this is the
    // assertion that must never soften into "fall back to the browsed dir".
    expect(onPick).not.toHaveBeenCalled()
    // The rejected text stays in the bar so the user can fix the typo instead of retyping it.
    expect(pathBar()).toHaveValue('/definitely/not/here/xyz')
  })

  it('says which path it will use while a typed draft is pending', async () => {
    // The git note is derived from the BROWSED dir, so it cannot describe a typed path — it read
    // "· not a git repo" identically for /etc, a ../ traversal and a nonexistent dir, while the
    // button never disabled. While a draft is pending it must not make a claim about a directory
    // the button is not going to bind.
    renderPicker()
    await loaded()
    expect(readsInFooter(/not a git repo/)).toBe(true)

    await typePath('/tmp')

    expect(readsInFooter(/will use \/tmp/)).toBe(true)
    expect(readsInFooter(/not a git repo/)).toBe(false)
  })
})

describe('a slow first browse', () => {
  it('does not overwrite a path the user has already started typing', async () => {
    // Found by driving the fix on a loaded machine: the initial browse-dirs response landed AFTER
    // the keystrokes, reset the bar to the default browse location (the user's HOME) and the button
    // bound THAT. Same silent substitution as the bug this file is about, through a different door,
    // and the window widens with a slow filesystem or a big home directory.
    let release: () => void = () => {}
    const held = new Promise<void>((r) => { release = r })
    browseDirs.mockImplementationOnce(async () => {
      await held
      return { path: HOME, parent: '/Users', in_repo: false, dirs: [{ name: 'Projects', path: `${HOME}/Projects` }] }
    })

    const onPick = renderPicker()
    // The bar is empty and the mount browse has NOT resolved — the user types anyway.
    await userEvent.type(pathBar(), '/tmp')
    release()
    await waitFor(() => expect(screen.queryAllByText(/Projects/).length).toBeGreaterThan(0))

    // Their path survived the arriving response…
    expect(pathBar()).toHaveValue('/tmp')
    // …and is what gets bound.
    await userEvent.click(useButton())
    await waitFor(() => expect(onPick).toHaveBeenCalledWith('/private/tmp'))
    expect(onPick).not.toHaveBeenCalledWith(HOME)
  })

  it('still fills the bar when the user has NOT typed anything', async () => {
    renderPicker()
    await loaded()
    expect(pathBar()).toHaveValue(HOME)
  })
})

describe('the paths that already worked', () => {
  it('binds the browsed directory when the bar was never touched', async () => {
    const onPick = renderPicker()
    await loaded()

    await userEvent.click(useButton())

    expect(onPick).toHaveBeenCalledWith(HOME)
  })

  it('keeps Enter-then-click working, and does not re-resolve what Enter already navigated to', async () => {
    // Enter commits the draft by navigating — the documented workaround for this bug. It must
    // still bind the typed path, and the click after it must not fire a second browse.
    const onPick = renderPicker()
    await loaded()

    await typePath('/tmp{Enter}')
    await waitFor(() => expect(pathBar()).toHaveValue('/private/tmp'))
    const afterEnter = browseDirs.mock.calls.length

    await userEvent.click(useButton())

    expect(onPick).toHaveBeenCalledWith('/private/tmp')
    expect(browseDirs.mock.calls).toHaveLength(afterEnter)
  })

  it('binds a folder picked by its row Use button, ignoring an unrelated draft', async () => {
    // The row button names its own directory, so a stale draft must not hijack it.
    const onPick = renderPicker()
    await loaded()

    await typePath('/tmp')
    // By title: the row control's accessible name is the bare word "Use" (its text), and the
    // directory it acts on lives in the title — which is what distinguishes it from the footer's.
    await userEvent.click(screen.getByTitle('Use Projects as the codebase'))

    expect(onPick).toHaveBeenCalledWith(`${HOME}/Projects`)
  })
})
