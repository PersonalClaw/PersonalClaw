import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { DurabilityPanel } from './DurabilityPanel'
import { invalidateKeys } from '../../lib/data'
import {
  api,
  type DurabilityArchive,
  type DurabilityArchives,
  type DurabilityStatus,
  type DurabilitySyncStatus,
  type SettingsProvider,
} from '../../lib/api'

// ── The Backups page shows what actually ran ─────────────────────────────────
//
// Two ways this page told a user something that was not so:
//
//  • A sync that failed read "Last sync just now · Shards leaving this machine encrypted". The run
//    had sent nothing: encryption was on for the folder, no passphrase was saved, and the only place
//    that said so was the gateway log. The card must say the run FAILED, why, in plain words, and —
//    since a passphrase is what it needs — take one right there, before the first run as well as after.
//  • After "Run now › Export" or "Snapshot" succeeded, the page kept the old "Last run" and the old
//    archive list until a reload: the run's answer was a toast, and nothing re-read the status.

const NOW = () => Math.floor(Date.now() / 1000)
const CREDENTIAL = 'PERSONALCLAW_SYNC_PASSPHRASE'
const PASSPHRASE = 'invented passphrase typed in a test'

function sync(over: Partial<DurabilitySyncStatus> = {}): DurabilitySyncStatus {
  return {
    last_run: 0, due_in_secs: 0, due: false, enabled: true,
    transport: 'dir-sync', encrypt: 'auto', encrypted: true,
    ok: null, last_success: 0, problem: null, skipped: '',
    passphrase_credential: CREDENTIAL, passphrase_stored: false,
    removes_old_copies: true, keeps_previous_secs: 900, removal_failed: '',
    ...over,
  }
}

function status(over: Partial<DurabilityStatus> = {}): DurabilityStatus {
  return {
    enabled: true,
    export: { last_run: 0, due_in_secs: 0, due: false },
    snapshot: { last_run: 0, due_in_secs: 0, due: false },
    drill: { last_run: 0, due_in_secs: 0, due: false },
    sync: sync(),
    ...over,
  }
}

function archive(name: string): DurabilityArchive {
  return { id: name, name, taken_at: '2026-09-30T07:00:00Z', size: 2048, retained: true, domains: null, validate: null }
}

function archives(list: DurabilityArchive[]): DurabilityArchives {
  return {
    directory: '/tmp/snapshots', archives: list, would_prune: [],
    tiers: { daily: 14, weekly: 8, monthly: 12 },
    last_drill: { ran: false, ok: null, at: 0, detail: '', archive: '' },
  }
}

const FOLDER = { name: 'dir-sync', displayName: 'Folder Sync', enabled: true, provider: { type: 'sync' } } as unknown as SettingsProvider

/** Every read the panel makes, stubbed. `statuses` and `snaps` are answered in order, the last
 *  one repeating, so a test can say what the page should show after it re-reads. */
function stubPanel(statuses: DurabilityStatus[], snaps: DurabilityArchives[] = [archives([])]) {
  const nextStatus = [...statuses]
  const nextSnaps = [...snaps]
  vi.spyOn(api, 'personalclawConfig').mockResolvedValue({
    durability: { auto_backup: true, sync_enabled: true, sync_transport: 'dir-sync', sync_encrypt: 'auto' },
  })
  const statusCall = vi.spyOn(api, 'durabilityStatus').mockImplementation(() =>
    Promise.resolve(nextStatus.length > 1 ? nextStatus.shift()! : nextStatus[0]))
  const archiveCall = vi.spyOn(api, 'durabilityArchive').mockImplementation(() =>
    Promise.resolve(nextSnaps.length > 1 ? nextSnaps.shift()! : nextSnaps[0]))
  vi.spyOn(api, 'settingsProviders').mockResolvedValue([FOLDER])
  vi.spyOn(api, 'durabilityConflicts').mockResolvedValue({
    conflicts: [], truncated: false,
    counts: { total: 0, needs_review: 0, by_surface: {}, selected: 0 },
    surfaces: { memory: 'memory', knowledge: 'knowledge', durability: 'durability' },
    sync: { enabled: true, transport: 'dir-sync', configured: true },
  })
  vi.spyOn(api, 'durabilityHistory').mockResolvedValue({ enabled: false, git: true, dir: '/tmp/h', roots: [] })
  vi.spyOn(api, 'durabilityHistoryTimeline').mockResolvedValue({ root: 'config', label: 'Configuration', commits: 0, entries: [], forward_refs: [] })
  return { statusCall, archiveCall }
}

/** The line a `JobLine` renders: its label and, beside it, what it says. */
function jobLine(label: string): string {
  const el = screen.getByText(label, { selector: 'span' })
  return el.parentElement?.textContent ?? ''
}

beforeEach(() => {
  invalidateKeys('settings:durability')
  invalidateKeys('settings:history')
  invalidateKeys('settings:history:config:all')
})

afterEach(() => { vi.restoreAllMocks() })

describe('Run now shows the run it just did', () => {
  it('re-reads the status after an export, so "Last run" is the run just made', async () => {
    const before = status({ export: { last_run: NOW() - 22 * 60, due_in_secs: 2280, due: false } })
    const after = status({ export: { last_run: NOW(), due_in_secs: 3600, due: false } })
    const { statusCall } = stubPanel([before, after])
    vi.spyOn(api, 'durabilityRun').mockResolvedValue({ job: 'export', ok: true, skipped: '', detail: 'exported', duration_secs: 1 })

    render(<DurabilityPanel />)
    await waitFor(() => expect(jobLine('Incremental export')).toContain('22 min ago'))
    const reads = statusCall.mock.calls.length

    fireEvent.click(screen.getByRole('button', { name: /^export$/i }))

    await waitFor(() => expect(statusCall.mock.calls.length).toBeGreaterThan(reads))
    await waitFor(() => expect(jobLine('Incremental export')).toContain('just now'))
  })

  it('re-reads the archive after a snapshot, so the new archive is listed', async () => {
    const old = 'personalclaw-snapshot-20260929T045552Z.tar.gz'
    const fresh = 'personalclaw-snapshot-20260930T070105Z.tar.gz'
    stubPanel([status()], [archives([archive(old)]), archives([archive(fresh), archive(old)])])
    vi.spyOn(api, 'durabilityRun').mockResolvedValue({ job: 'snapshot', ok: true, skipped: '', detail: 'snapshot taken', duration_secs: 24 })

    render(<DurabilityPanel />)
    await screen.findByText(old)
    expect(screen.queryByText(fresh)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /^snapshot$/i }))

    expect(await screen.findByText(fresh)).toBeTruthy()
  })
})

describe('the Sync card says a failed run failed', () => {
  it('reads a failed run as failed, with the reason in plain words', async () => {
    const failed = sync({
      last_run: NOW() - 30, ok: false,
      problem: {
        code: 'passphrase',
        message: 'Nothing was synced: shards are encrypted for this transport, and no sync passphrase was saved on this machine.',
        remedy: 'Save a sync passphrase under Settings → Backups → Sync, and use the same one on every machine that syncs with this one.',
        since: NOW() - 30, failures: 1,
      },
    })
    stubPanel([status({ sync: failed })])

    render(<DurabilityPanel />)

    await waitFor(() => expect(jobLine('Last sync')).toMatch(/failed just now/))
    expect(jobLine('Last sync')).not.toMatch(/^Last syncjust now/)
    expect(screen.getByText(/nothing was synced: shards are encrypted/i)).toBeTruthy()
    // Nothing left this machine, so "encrypted" alone would be a claim about bytes that never went.
    expect(jobLine('Shards leaving this machine')).toMatch(/nothing leaves this machine until a passphrase is saved/i)
  })

  it('says how long a sync has kept failing, and what to do', async () => {
    const failing = sync({
      last_run: NOW() - 60, ok: false, passphrase_stored: true,
      problem: {
        code: 'pull', message: 'Nothing was synced: reading the shared store failed (the folder is not mounted).',
        remedy: 'Check the transport’s settings under Settings → Providers and that its storage is reachable; the next sync tries again.',
        since: NOW() - 3600, failures: 4,
      },
    })
    stubPanel([status({ sync: failing })])

    render(<DurabilityPanel />)

    await screen.findByText(/the folder is not mounted/)
    expect(jobLine('Last sync')).toMatch(/4 runs in a row/)
    expect(screen.getByText(/storage is reachable/)).toBeTruthy()
  })
})

describe('the Sync card takes the passphrase it needs', () => {
  it('asks for one before the first run, and saves it write-only to the credential store', async () => {
    const { statusCall } = stubPanel([status(), status({ sync: sync({ passphrase_stored: true }) })])
    const put = vi.spyOn(api, 'putSecret').mockResolvedValue({ secret: {}, secrets: [] })

    const { container } = render(<DurabilityPanel />)

    const field = await screen.findByLabelText('Sync passphrase')
    expect((field as HTMLInputElement).type).toBe('password')
    const prompt = screen.getByRole('group', { name: 'Encryption needs a passphrase' })
    expect(within(prompt).getByText(/nothing syncs until a passphrase is saved/i)).toBeTruthy()
    expect(within(prompt).getByText(/same passphrase on every machine/i)).toBeTruthy()

    fireEvent.change(field, { target: { value: PASSPHRASE } })
    const reads = statusCall.mock.calls.length
    fireEvent.click(within(prompt).getByRole('button', { name: /save passphrase/i }))

    await waitFor(() => expect(put).toHaveBeenCalledWith(CREDENTIAL, PASSPHRASE))
    // It goes to the credential store once and is never rendered back.
    await waitFor(() => expect(statusCall.mock.calls.length).toBeGreaterThan(reads))
    await waitFor(() => expect(screen.queryByLabelText('Sync passphrase')).toBeNull())
    expect(container.innerHTML).not.toContain(PASSPHRASE)
    expect(jobLine('Sync passphrase')).toMatch(/saved in the credential store/)
    expect(jobLine('Shards leaving this machine')).toMatch(/encrypted$/)
  })

  it('keeps the field and says why when the save is refused', async () => {
    stubPanel([status()])
    vi.spyOn(api, 'putSecret').mockRejectedValue(new Error('the credential store is read-only'))

    render(<DurabilityPanel />)
    const field = await screen.findByLabelText('Sync passphrase')
    fireEvent.change(field, { target: { value: PASSPHRASE } })
    fireEvent.click(screen.getByRole('button', { name: /save passphrase/i }))

    expect(await screen.findByText(/the credential store is read-only/)).toBeTruthy()
    expect(screen.getByLabelText('Sync passphrase')).toBeTruthy()
  })

  it('does not ask when encryption is off for the transport', async () => {
    stubPanel([status({ sync: sync({ encrypted: false, encrypt: 'off' }) })])

    render(<DurabilityPanel />)
    await waitFor(() => expect(jobLine('Shards leaving this machine')).toMatch(/readable/))
    expect(screen.queryByLabelText('Sync passphrase')).toBeNull()
  })

  it('re-reads the status when the transport changes, so the prompt follows the choice', async () => {
    const { statusCall } = stubPanel([status({ sync: sync({ transport: '', encrypted: false }) }), status()])
    vi.spyOn(api, 'patchConfig').mockResolvedValue({})

    render(<DurabilityPanel />)
    const select = await screen.findByLabelText('Sync transport')
    const reads = statusCall.mock.calls.length
    fireEvent.change(select, { target: { value: 'dir-sync' } })

    await waitFor(() => expect(statusCall.mock.calls.length).toBeGreaterThan(reads))
    expect(await screen.findByLabelText('Sync passphrase')).toBeTruthy()
  })
})

// Every sync sent this machine's records as a whole new copy into the shared store, and nothing
// ever removed one, so a folder chosen because it syncs itself took a full copy every fifteen
// minutes, for good — and the card said nothing of it. It now says which copies the store keeps.
describe('the Sync card says which copies the store keeps', () => {
  it('a transport that removes old copies keeps the newest, and the one before it a while', async () => {
    stubPanel([status({ sync: sync({ passphrase_stored: true, keeps_previous_secs: 900 }) })])

    render(<DurabilityPanel />)

    await waitFor(() => expect(jobLine('Copies kept in the store'))
      .toMatch(/this machine's newest, and the one before it for 15 min$/))
    expect(screen.getByText(/sent only when your records change, and one a newer copy replaced is then removed/))
      .toBeTruthy()
    expect(screen.getByText(/a synced folder’s trash, a versioned bucket/)).toBeTruthy()
  })

  it('a transport that keeps every copy says so', async () => {
    stubPanel([status({ sync: sync({ passphrase_stored: true, removes_old_copies: false }) })])

    render(<DurabilityPanel />)

    await waitFor(() => expect(jobLine('Copies kept in the store')).toMatch(/every copy this machine sends$/))
    expect(screen.getByText(/This transport removes none of them/)).toBeTruthy()
    expect(screen.queryByText(/is then removed/)).toBeNull()
  })

  it('says when the last sync could not remove older copies, so the line above is not taken as done', async () => {
    stubPanel([status({ sync: sync({ passphrase_stored: true, removal_failed: 'the sync folder is busy' }) })])

    render(<DurabilityPanel />)

    expect(await screen.findByText(/couldn’t remove older copies \(the sync folder is busy\)\. They stay/))
      .toBeTruthy()
  })

  it('says nothing of a transport that is not installed, or of none', async () => {
    stubPanel([status({ sync: sync({ passphrase_stored: true, removes_old_copies: null }) })])

    const { unmount } = render(<DurabilityPanel />)
    await waitFor(() => expect(jobLine('Shards leaving this machine')).toMatch(/encrypted$/))
    expect(screen.queryByText('Copies kept in the store')).toBeNull()
    unmount()

    invalidateKeys('settings:durability')
    stubPanel([status({ sync: sync({ transport: '', encrypted: false }) })])
    render(<DurabilityPanel />)
    await waitFor(() => expect(jobLine('Shards leaving this machine')).toMatch(/no transport chosen/))
    expect(screen.queryByText('Copies kept in the store')).toBeNull()
  })
})
