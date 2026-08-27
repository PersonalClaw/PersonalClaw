import { describe, expect, it, vi, afterEach } from 'vitest'
import { act, render, screen, cleanup } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The "move to keychain" action must SHOW the snapshot before it moves anything ──────────
//
// SH-2's done_when says the Settings action "runs the migration with a visible snapshot-confirm
// step". That is a claim about a dialog, and a dialog body that describes backend behaviour
// decays into confident fiction unless something fails when the behaviour changes — so this
// file asserts BOTH halves: what the user is told, and that the Python does it.
//
// 🪤 THE GATE IS ASSERTED FIRST. The interesting failure is not a wrong sentence, it is a
// button that migrates the user's credentials with no dialog at all: the confirm resolving
// FALSE must leave the API untouched. Every assertion here would still pass if the dialog were
// cosmetic, so that one is the load-bearing one.

/** The subset of `ConfirmOptions` this surface passes — typed so `mock.calls[0][0]` needs no
 *  cast, which is what let a `calls[0]` on an empty tuple compile in the first draft. */
interface ConfirmRequest { title: string; body: string; confirmLabel?: string; danger?: boolean }

const PY = join(__dirname, '../../../../src/personalclaw')
const py = (rel: string) => readFileSync(join(PY, rel), 'utf8')

const STATE = {
  migration: 'credentials_to_keychain',
  backend: 'keychain' as const,
  requested: 'keychain' as const,
  blocked: false,
  pending_keys: ['SH2_ALPHA', 'SH2_BETA'],
  pending: 2,
  keychain_keys: 0,
  rollback_available: false,
  snapshot_name: '.env.pre-keychain',
  verified: true,
  verification: { checked: 0, missing: [], still_in_dotenv: [] },
}

type StateOverride = Partial<Omit<typeof STATE, 'backend' | 'requested'>>
  & { backend?: 'keychain' | 'dotenv'; requested?: 'keychain' | 'dotenv' }

async function mount(opts: { confirmed: boolean; state?: StateOverride } = { confirmed: true }) {
  vi.resetModules()
  sessionStorage.clear()
  const migrate = vi.fn(() => Promise.resolve({
    ...STATE, ...opts.state, ok: true, reason: '', moved: ['SH2_ALPHA', 'SH2_BETA'],
    already: [], failed: [], pending: 0, pending_keys: [], rollback_available: true,
  }))
  const rollback = vi.fn(() => Promise.resolve({
    ...STATE, ...opts.state, ok: true, reason: '', moved: ['SH2_ALPHA'], already: [], failed: [],
  }))
  const confirmSpy = vi.fn((_req: ConfirmRequest) => Promise.resolve(opts.confirmed))
  vi.doMock('../../ui/dialog', () => ({ confirm: confirmSpy }))
  vi.doMock('../../lib/api', () => ({
    api: {
      securityStats: () => Promise.resolve({
        denied_commands: 1, suspicious_patterns: 1, tool_schemas: 1, redaction_paths: 1,
      }),
      deniedCommands: () => Promise.resolve({
        builtin: ['rm -rf'], user: [], user_additions: 0,
        baseline: { version: '1', pattern_count: 1, sha256: 'a'.repeat(64), verified: true, user_additions: 0 },
      }),
      securityEgress: () => Promise.resolve({ allow_hosts: [], deny_hosts: [], allow_private: false }),
      desktopState: () => Promise.resolve({
        connected: false, shell: null, capabilities: {}, registered_at: '', last_seen: '',
      }),
      credentialStore: () => Promise.resolve({ ...STATE, ...opts.state }),
      migrateCredentialsToKeychain: migrate,
      rollbackCredentialsToKeychain: rollback,
      setCredentialKeychain: () => Promise.resolve({}),
      setUserDeniedCommands: () => Promise.resolve({}),
      setSecurityEgress: () => Promise.resolve({}),
    },
  }))
  const { SecurityPanel } = await import('./SecurityPanel')
  await act(async () => {
    render(<SecurityPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return { migrate, rollback, confirmSpy }
}

afterEach(() => { cleanup(); vi.resetModules() })

describe('the move to keychain is gated on a visible snapshot confirm', () => {
  it('a declined confirm moves nothing', async () => {
    const { migrate, confirmSpy } = await mount({ confirmed: false })
    const btn = screen.getByRole('button', { name: /move 2 to keychain/i })
    await act(async () => { btn.click(); await new Promise((r) => setTimeout(r, 0)) })
    expect(confirmSpy, 'the dialog was actually opened').toHaveBeenCalledTimes(1)
    expect(migrate, 'declining must not touch the credential store').not.toHaveBeenCalled()
  })

  it('an accepted confirm runs the migration exactly once', async () => {
    const { migrate, confirmSpy } = await mount({ confirmed: true })
    const btn = screen.getByRole('button', { name: /move 2 to keychain/i })
    await act(async () => { btn.click(); await new Promise((r) => setTimeout(r, 0)) })
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(migrate).toHaveBeenCalledTimes(1)
  })

  it('the dialog names the snapshot file, the keys, and the reversal', async () => {
    const { confirmSpy } = await mount({ confirmed: false })
    await act(async () => {
      screen.getByRole('button', { name: /move 2 to keychain/i }).click()
      await new Promise((r) => setTimeout(r, 0))
    })
    const req = confirmSpy.mock.calls[0][0]
    expect(req.danger, 'moving a user\'s secrets is a destructive-weight action').toBe(true)
    expect(req.title).toMatch(/Move 2 credentials into the OS keychain\?/)
    expect(req.body, 'the snapshot is named, not implied').toContain('.env.pre-keychain')
    expect(req.body, 'and its mode is stated').toContain('0600')
    expect(req.body, 'the keys are named — the user sees exactly what moves').toContain('SH2_ALPHA, SH2_BETA')
    expect(req.body).toMatch(/removed from \.env/)
    expect(req.body, 'and that it is reversible').toMatch(/Roll back/)
  })
})

describe('the dialog body is TRUE of the handler', () => {
  it('the snapshot really is written first, at 0600, before any keychain write', () => {
    const impl = py('config/credential_migration.py')
    const snap = impl.slice(impl.indexOf('def _write_snapshot'), impl.indexOf('def migrate_credentials_to_keychain'))
    expect(snap, 'the filename the dialog names').toContain('rollback_snapshot_path()')
    expect(snap, 'at the mode the dialog names').toContain('mode=0o600')
    expect(snap, 'and written once — a second run must not clobber it').toMatch(/if snap\.exists\(\):\s*\n\s*return/)
    expect(impl).toContain('ROLLBACK_FILENAME = ".env.pre-keychain"')
    // 🪤 ORDERING IS THE CLAIM. "Your .env is copied FIRST" is false if the snapshot is
    // written after the first key moves, and nothing else in this file would notice.
    const mig = impl.slice(impl.indexOf('def migrate_credentials_to_keychain'))
    expect(mig.indexOf('_write_snapshot()')).toBeGreaterThan(-1)
    expect(mig.indexOf('_write_snapshot()'), 'before the first keychain write')
      .toBeLessThan(mig.indexOf('_keychain_save('))
  })

  it('no key is removed from .env until its value reads back out of the keychain', () => {
    const impl = py('config/credential_migration.py')
    const mig = impl.slice(impl.indexOf('def migrate_credentials_to_keychain'))
    const verify = mig.indexOf('if _keychain_get(key) != value:')
    const remove = mig.indexOf('_dotenv_remove_credentials(')
    expect(verify, 'the read-back exists').toBeGreaterThan(-1)
    expect(verify, 'and precedes the removal').toBeLessThan(remove)
    expect(mig, 'a key that fails verification is left behind').toMatch(/failed\.append\(key\)\n\s*continue/)
  })

  it('the rollback restores the snapshot BYTES, which is what "exactly" means', () => {
    const impl = py('config/credential_migration.py')
    const rb = impl.slice(impl.indexOf('def rollback_credentials_to_keychain'))
    expect(rb).toContain('raw = snap.read_bytes()')
    expect(rb, 'written verbatim, not re-serialised from parsed keys')
      .toContain('atomic_write_bytes(_loader.env_path(), raw, mode=0o600, fsync=True)')
  })

  it('both writes refuse without the confirm the dialog represents', () => {
    const impl = py('config/credential_migration.py')
    // The client always sends `confirm: true`; the point is that the CORE refuses without it,
    // so a second caller cannot skip the consent by not knowing about it.
    expect((impl.match(/if not confirm:/g) ?? []).length, 'migrate AND rollback').toBe(2)
    const h = py('dashboard/handlers/security_credentials.py')
    expect((h.match(/if not await _confirmed\(request\):/g) ?? []).length).toBe(2)
    expect(h, 'a malformed body is a NO, never a yes').toMatch(/return isinstance\(body, dict\) and body\.get\("confirm"\) is True/)
  })
})

describe('the panel cannot offer a move that would refuse', () => {
  it('a blocked backend disables the button and says why', async () => {
    await mount({ confirmed: true, state: { blocked: true, backend: 'dotenv' } })
    const btn = screen.getByRole('button', { name: /move 2 to keychain/i })
    // `disabledReason` keeps the control REACHABLE (aria-disabled) so a keyboard user learns
    // the precondition instead of tabbing past a dead button.
    expect(btn.getAttribute('aria-disabled')).toBe('true')
    expect(btn.getAttribute('title')).toMatch(/Store credentials in the OS keychain/)
    expect(screen.getByText(/no usable OS keyring backend answered/i)).toBeTruthy()
  })

  it('nothing pending disables it with a different reason — the vacuity floor', async () => {
    await mount({ confirmed: true, state: { pending: 0, pending_keys: [] } })
    const btn = screen.getByRole('button', { name: /move to keychain/i })
    expect(btn.getAttribute('title')).toMatch(/no credentials left in \.env/i)
  })

  it('the roll-back action appears only when a snapshot exists', async () => {
    await mount({ confirmed: true })
    expect(screen.queryByRole('button', { name: /roll back/i }), 'no snapshot yet').toBeNull()
    cleanup()
    await mount({ confirmed: true, state: { rollback_available: true } })
    expect(screen.getByRole('button', { name: /roll back/i })).toBeTruthy()
  })
})
