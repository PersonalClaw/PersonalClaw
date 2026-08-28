import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, render, screen, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// ── EI-10 · the secrets vault shows PRESENCE, and renders its three scopes as three things ──
//
// The backend guarantees no value can reach this panel (`SecretPresenceWire` has no value field,
// and `/api/secrets` builds rows from key names only). That makes the FRONTEND's job a different
// one, and these are the clauses worth pinning because each is trivial to get wrong in a way that
// still looks fine:
//
//   • **A host row is not a vault row.** Its value lives in the gateway's environment, so the
//     vault cannot rotate or remove it. Rendered identically to a stored secret, the UI would
//     promise management it cannot deliver. Pinned by ROLE and by the disabled Remove control's
//     announced reason — not by reading the JSX.
//   • **"Not referenced by anything" is said out loud.** A blank "used by" line is ambiguous
//     between "safe to delete" and "we didn't check", and the first is advice.
//   • **The empty state carries the SERVER's sentence.** A hardcoded "No secrets yet" in the
//     panel would drift from the CLI and would read as "secrets are broken" on this page. The
//     test asserts the server's hint text appears, so a hardcoded local copy fails.
//   • **A failed read renders LoadError, not an empty vault.** This is the one that matters most
//     here: "you have no secrets" and "the fetch failed" are pixel-identical otherwise, and on
//     this page the first is a dangerous lie. `role="alert"` is the measurable difference.
//
// 🪤 COLD sessionStorage per test. `useQuery(…, { persist: true })` seeds from sessionStorage and
// the settings hub writes a substituted `null` under `settings:secrets-card`; a warm cache would
// mask both the failure branch and a fresh payload.
//
// 🔑 THE PANEL'S SOURCE IS PINNED TOO (last test): the value input must be `type="password"` and
// the panel must never declare a value field. A copy edit that added a "reveal" affordance would
// be requesting a field the server cannot send, and it should fail here rather than ship dead.

// Resolved from THIS FILE, not from `process.cwd()`. The cwd differs between `npm run test:web`
// (which runs in `web/`) and a `vitest --root web` invocation from the repo root, and a path that
// only works under one of them turns a source rail into an ENOENT nobody reads as a real failure.
const HERE = dirname(fileURLToPath(import.meta.url))
const PANEL = join(HERE, 'SecretsPanel.tsx')
const API = join(HERE, '../../lib/api.ts')

const HINT =
  'No secrets stored yet. Add one here, then reference it from a workflow or automation '
  + 'as {{secret:NAME}} — the value is written once and never read back out.'

/** Source with `//` and block comments removed, for the rails that must read CODE only. */
const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

type Row = {
  name: string
  scope: 'global' | 'project' | 'host'
  project_id?: string
  consumers?: { kind: 'workflow' | 'trigger'; id: string; label: string }[]
}

const row = (r: Row) => ({
  name: r.name,
  scope: r.scope,
  project_id: r.project_id ?? '',
  present: true as const,
  inherited_from_host: r.scope === 'host',
  consumers: r.consumers ?? [],
})

const payload = (rows: Row[]) => {
  const secrets = rows.map(row)
  return {
    secrets,
    counts: {
      total: secrets.length,
      global: secrets.filter((s) => s.scope === 'global').length,
      project: secrets.filter((s) => s.scope === 'project').length,
      host: secrets.filter((s) => s.scope === 'host').length,
    },
    empty_hint: secrets.length === 0 ? HINT : '',
  }
}

const putSecret = vi.fn()
const deleteSecret = vi.fn()

/** Mount the panel over a faked `api`. Dynamic import so `vi.doMock` takes effect. */
async function mount(secrets: () => Promise<unknown>) {
  vi.resetModules()
  sessionStorage.clear()
  putSecret.mockReset().mockResolvedValue({ secret: {}, secrets: [] })
  deleteSecret.mockReset().mockResolvedValue({ deleted: '', project_id: '', secrets: [] })
  vi.doMock('../../ui/dialog', () => ({ confirm: vi.fn(async () => true) }))
  vi.doMock('../../lib/api', () => ({ api: { secrets, putSecret, deleteSecret } }))
  const { SecretsPanel } = await import('./SecretsPanel')
  await act(async () => {
    render(<SecretsPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
}

beforeEach(() => { sessionStorage.clear() })
afterEach(() => { cleanup(); vi.resetModules(); vi.restoreAllMocks() })

describe('a stored secret renders as presence, never as a value', () => {
  it('shows the name and a "set" marker, and no value anywhere in the DOM', async () => {
    await mount(async () => payload([
      { name: 'GITHUB_TOKEN', scope: 'global' },
      { name: 'DB_PASSWORD', scope: 'project', project_id: 'proj-a' },
    ]))
    // 🪤 VACUITY FLOOR — assert the rows actually rendered before concluding anything from the
    // absence of a value. An unmounted panel contains no value either.
    expect(screen.getByText('GITHUB_TOKEN')).toBeTruthy()
    expect(screen.getByText('DB_PASSWORD')).toBeTruthy()
    expect(screen.getAllByText(/^set$/i).length).toBe(2)
    // The project row says whose it is.
    expect(screen.getByText('proj-a')).toBeTruthy()
  })

  it('says out loud when nothing references a secret', async () => {
    await mount(async () => payload([{ name: 'UNUSED_TOKEN', scope: 'global' }]))
    expect(screen.getByText(/not referenced by any workflow or automation/i)).toBeTruthy()
  })

  it('renders each derived consumer link', async () => {
    await mount(async () => payload([{
      name: 'GITHUB_TOKEN',
      scope: 'global',
      consumers: [
        { kind: 'workflow', id: 'nightly-sync', label: 'Nightly sync' },
        { kind: 'trigger', id: 't-1', label: 'Morning digest' },
      ],
    }]))
    expect(screen.getByText(/used by/i)).toBeTruthy()
    expect(screen.getByText('Nightly sync')).toBeTruthy()
    expect(screen.getByText('Morning digest')).toBeTruthy()
    expect(screen.queryByText(/not referenced by any/i)).toBeNull()
  })
})

describe('an inherit-from-host row is not a vault row', () => {
  it('is marked as coming from the host environment', async () => {
    await mount(async () => payload([{ name: 'SSH_AUTH_SOCK', scope: 'host' }]))
    expect(screen.getByText('SSH_AUTH_SOCK')).toBeTruthy()
    expect(screen.getByText(/from host environment/i)).toBeTruthy()
    // And it does NOT claim to be a stored vault secret.
    expect(screen.queryByText(/^set$/i)).toBeNull()
  })

  it('its Remove control is reachable but refuses, WITH the reason', async () => {
    await mount(async () => payload([{ name: 'SSH_AUTH_SOCK', scope: 'host' }]))
    const remove = screen.getByRole('button', { name: /remove/i })
    // aria-disabled, not the native attribute: a natively disabled control leaves the tab order,
    // so a keyboard user could never discover WHY it is off.
    expect(remove.getAttribute('aria-disabled')).toBe('true')
    expect(remove.getAttribute('title') ?? '').toMatch(/gateway's environment/i)
    await act(async () => { remove.click() })
    expect(deleteSecret).not.toHaveBeenCalled()
  })

  it('a stored secret CAN be removed — so the refusal above is about the scope, not the button', async () => {
    // 🪤 The vacuity partner for the two assertions above. If Remove were disabled for every row,
    // both would pass while the control was simply broken.
    await mount(async () => payload([{ name: 'GITHUB_TOKEN', scope: 'global' }]))
    const remove = screen.getByRole('button', { name: /remove/i })
    expect(remove.getAttribute('aria-disabled')).toBeNull()
    await act(async () => { remove.click() })
    expect(deleteSecret).toHaveBeenCalledWith('GITHUB_TOKEN', '')
  })
})

describe('the honest read', () => {
  it('an empty vault carries the SERVER\'s next-action sentence', async () => {
    await mount(async () => payload([]))
    expect(screen.getByText(/no secrets yet/i)).toBeTruthy()
    // 🪤 Assert clauses UNIQUE TO THE SERVER's sentence. The add-form's own hint also teaches the
    // `{{secret:NAME}}` syntax (deliberately — it belongs beside the field), so matching that
    // shared clause found two nodes and told us nothing about where the copy came from.
    expect(screen.getByText(/no secrets stored yet\. add one here/i)).toBeTruthy()
    expect(screen.getByText(/never read back out/i)).toBeTruthy()
    // An empty vault is NOT an error state.
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('a failed read renders an alert, never an empty vault', async () => {
    await mount(async () => { throw new Error('boom') })
    const alert = screen.getByRole('alert')
    expect(within(alert).getByText(/couldn't load your secrets vault/i)).toBeTruthy()
    // 🔑 The lie this prevents: "you have no secrets" on a failed fetch.
    expect(screen.queryByText(/no secrets yet/i)).toBeNull()
  })
})

describe('the write path is one-way, in the source as well as at runtime', () => {
  it('the value input is masked and the panel declares no value field', () => {
    const src = readFileSync(PANEL, 'utf8')
    expect(src, 'the value field must be masked').toMatch(/type="password"/)
    // 🪤 SCAN THE CODE, NOT THE COMMENTS. The panel's own docstring explains why there is no
    // "reveal" affordance, and the first draft of this assertion matched that explanation — a text
    // scanner reading prose reported the absence it was asserting as a violation.
    expect(codeOnly(src)).not.toMatch(/\breveal\b/i)
    expect(codeOnly(src)).not.toMatch(/navigator\.clipboard/)
    // Vacuity floor: stripping must leave real code behind, or the two absences are trivial.
    expect(codeOnly(src)).toMatch(/type="password"/)
  })

  it('the wire type has no value field', () => {
    const src = readFileSync(API, 'utf8')
    const iface = /export interface SecretPresenceWire \{([^}]*)\}/.exec(src)?.[1] ?? ''
    // 🪤 VACUITY: the regex must actually have matched something.
    expect(iface.length, 'SecretPresenceWire not found in api.ts').toBeGreaterThan(20)
    expect(iface).toMatch(/name: string/)
    expect(iface).not.toMatch(/\bvalue\b/)
    expect(iface).not.toMatch(/\bplaintext\b/)
  })

  it('there is no client method that reads a secret back', () => {
    const src = readFileSync(API, 'utf8')
    expect(src).not.toMatch(/getSecret\s*:/)
    expect(src).not.toMatch(/revealSecret\s*:/)
    // The three that DO exist — vacuity floor, so the absences above mean something.
    expect(src).toMatch(/\bsecrets:\s*\(projectId/)
    expect(src).toMatch(/\bputSecret:\s*\(/)
    expect(src).toMatch(/\bdeleteSecret:\s*\(/)
  })
})
