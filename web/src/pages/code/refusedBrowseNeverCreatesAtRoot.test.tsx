/** A refused browse must never leave the workspace picker able to create a folder at the filesystem ROOT.
 *
 * Reproduced three times in a real browser against a fresh container whose gateway ran as root: the
 * picker's first browse — the gateway's own home — answered `403 {"error": "Access denied"}`, and from
 * there every surface either lied or went quiet:
 *
 *   · the list read "No sub-folders here." — an empty state for a listing that was never read;
 *   · the red "Access denied" named no path and no reason, and vanished on the first keystroke;
 *   · "New folder here" stayed armed with an EMPTY current path, so `'' + '/' + 'q4-launch'` became
 *     `POST /api/create-dir {"path": "/q4-launch"}` → 200, and the project's workspace was bound to a
 *     brand-new root-owned folder at the top of the disk. Nothing on screen said where it went.
 *
 * The root-gateway refusal itself is fixed in the security layer, but a refused first browse is not
 * specific to it: a container started with a bare `--user` has `HOME=/`, and `/` stays refused. So the
 * fixture uses that one, and it drives the REAL api client through a stubbed `fetch` that answers with
 * the route's exact 403 body — what is asserted is the sentence the route writes reaching the user.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WorkspacePicker } from './WorkspacePicker'

const HOME = '/Users/me'

/** The body `api_browse_dirs` answers when the folder it was asked for is protected. */
const refusal = (path: string) => ({
  error: {
    code: 'path_protected',
    message: `Can't open ${path} — it's a protected system location.`,
    path,
    reason: 'system_root',
  },
})

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

type Call = { method: string; path: string; body?: { path?: string } }
let calls: Call[] = []

/** A stand-in gateway: `browse` answers browse-dirs (`null` = the default location), create-dir echoes. */
function gateway(browse: (path: string | null) => Response) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://gateway.test')
    const method = (init?.method || 'GET').toUpperCase()
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ method, path: url.pathname, body })
    if (url.pathname === '/api/browse-dirs') return browse(url.searchParams.get('path'))
    if (url.pathname === '/api/create-dir') return json(200, { ok: true, path: body.path })
    return json(404, { error: `unexpected route ${url.pathname}` })
  }))
}

/** The default location is refused, and nothing else is reachable — `HOME=/` in a bare-`--user` container. */
const refusedHome = () => gateway(() => json(403, refusal('/')))

/** A healthy home; only `/etc` is refused. */
const healthyHome = () => gateway((p) => {
  if (p === null || p === HOME) return json(200, { path: HOME, parent: '/Users', in_repo: false, dirs: [{ name: 'Projects', path: `${HOME}/Projects` }] })
  if (p === '/etc') return json(403, refusal('/etc'))
  return json(404, { error: 'No such directory', path: p })
})

function renderPicker() {
  const onPick = vi.fn()
  // The project-workspace bind shape — brownfield with create allowed — the one the report drove.
  render(<WorkspacePicker mode="brownfield" allowCreate onPick={onPick} onClose={() => {}} />)
  return onPick
}

const pathBar = () => screen.getByLabelText('Workspace path')
const newFolder = () => screen.getByRole('button', { name: /New folder here/ })
const creates = () => calls.filter((c) => c.path === '/api/create-dir')

beforeEach(() => { calls = [] })
afterEach(() => { vi.unstubAllGlobals() })

describe('a picker whose first browse is refused', () => {
  it('does not claim the folder it never read is empty', async () => {
    refusedHome()
    renderPicker()
    await screen.findByRole('alert')

    expect(screen.queryByText(/No sub-folders here/)).toBeNull()
    // It says what is actually true — nothing is open — and what to do about it.
    expect(screen.getByText(/No folder is open/)).toBeInTheDocument()
  })

  it('names the refused path and the reason', async () => {
    refusedHome()
    renderPicker()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("Can't open / — it's a protected system location.")
  })

  it('keeps the message while the user types', async () => {
    refusedHome()
    renderPicker()
    await screen.findByRole('alert')

    await userEvent.type(pathBar(), '/srv')

    expect(screen.getByRole('alert')).toHaveTextContent("Can't open / — it's a protected system location.")
  })

  it('does not offer to create a folder when there is no folder to create it in', async () => {
    refusedHome()
    renderPicker()
    await screen.findByRole('alert')

    // Reachable and explained, not silently dead — the kit's unavailable contract.
    expect(newFolder()).toHaveAttribute('aria-disabled', 'true')
    expect(newFolder()).toHaveAttribute('title', expect.stringMatching(/Open a folder first/))

    await userEvent.click(newFolder())

    // The name field never opens, so there is no "Create + use" to press…
    expect(screen.queryByPlaceholderText('new-project-folder')).toBeNull()
    expect(screen.queryByRole('button', { name: /Create \+ use/ })).toBeNull()
    // …and nothing was ever written anywhere, least of all at the root.
    expect(creates()).toEqual([])
  })
})

describe('a refusal after a folder is open', () => {
  it('keeps the folder being viewed, keeps the message while a new folder is named, and creates inside the viewed folder', async () => {
    healthyHome()
    const onPick = renderPicker()
    await waitFor(() => expect(pathBar()).toHaveValue(HOME))

    await userEvent.clear(pathBar())
    await userEvent.type(pathBar(), '/etc{Enter}')
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent("Can't open /etc"))
    // The listing is still the one that was read, so it still says so.
    expect(pathBar()).toHaveValue(HOME)
    expect(screen.getByText('Projects')).toBeInTheDocument()

    await userEvent.click(newFolder())
    await userEvent.type(screen.getByPlaceholderText('new-project-folder'), 'q4-launch')
    // Typing a folder NAME is not an answer to a refused NAVIGATION — the message stays.
    expect(screen.getByRole('alert')).toHaveTextContent("Can't open /etc")

    await userEvent.click(screen.getByRole('button', { name: /Create \+ use/ }))

    await waitFor(() => expect(onPick).toHaveBeenCalledWith(`${HOME}/q4-launch`))
    expect(creates().map((c) => c.body?.path)).toEqual([`${HOME}/q4-launch`])
  })
})
