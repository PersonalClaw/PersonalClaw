import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { DurabilityPanel } from './DurabilityPanel'
import { DialogHost } from '../../ui/dialog/DialogHost'
import { closeDialog, subscribeDialogs } from '../../ui/dialog/dialogStore'
import { invalidateKeys } from '../../lib/data'
import {
  api,
  type DurabilityHistoryEntry,
  type DurabilityHistoryPreview,
  type DurabilityHistoryTimeline,
} from '../../lib/api'

// ── Time travel: the preview is the gate (DURABILITY-AND-SYNC §5, DAS-9) ──────────────────────
//
// A rollback hard-resets a tree the user lives in. The contract is that no destructive call can
// be made without first HOLDING the preview — the confirming request must echo the
// `expected_head` the preview returned, and the server refuses one that does not match. So the
// interesting assertion here is not "a dialog appeared": it is that the apply call carries the
// head the PREVIEW produced, which a panel could not fabricate.
//
// Three ways this screen could look finished while lying:
//
//  • AN EMPTY TIMELINE IS INDISTINGUISHABLE FROM A BROKEN ONE. Almost every assertion about a
//    history list passes over an empty repo forever, so each one here sits behind a vacuity
//    floor: a seeded commit must RENDER first.
//  • A FILE TOO BIG TO DIFF AND A FILE WITH NO CHANGES look identical if the panel renders an
//    empty `diff` string. On a screen that gates a destructive action they must not.
//  • "WHAT CHANGED WHILE I SLEPT" is a filter whose empty result is also the normal result, so
//    the unattended entry is asserted to render its own words rather than merely be requested.

function entry(over: Partial<DurabilityHistoryEntry> = {}): DurabilityHistoryEntry {
  return {
    sha: 'a'.repeat(40),
    short: 'aaaaaaaa',
    at: Math.floor(Date.now() / 1000) - 600,
    subject: 'Configuration: 1 file changed',
    surface: 'interactive',
    unattended: false,
    ...over,
  }
}

function timeline(entries: DurabilityHistoryEntry[]): DurabilityHistoryTimeline {
  return {
    root: 'config',
    label: 'Configuration',
    commits: entries.length,
    entries,
    forward_refs: [],
  }
}

function preview(over: Partial<DurabilityHistoryPreview> = {}): DurabilityHistoryPreview {
  return {
    operation: 'rollback',
    root: 'config',
    target: 'a'.repeat(40),
    head: 'b'.repeat(40),
    files: [
      {
        path: 'config.json',
        status: 'M',
        bytes: 120,
        rendered: true,
        diff: '--- a/config.json\n+++ b/config.json\n-"theme": "dark"\n+"theme": "light"\n',
      },
    ],
    commits_rolled_away: 2,
    reversible: true,
    ...over,
  }
}

let previewCall: MockInstance<typeof api.durabilityHistoryPreview>
let applyCall: MockInstance<typeof api.durabilityHistoryApply>

/** Everything the panel loads, stubbed. Only the history calls matter here. */
function stubPanel(opts: {
  entries?: DurabilityHistoryEntry[]
  previewValue?: DurabilityHistoryPreview
  timelineError?: Error
  git?: boolean
} = {}) {
  const entries = opts.entries ?? [entry({ sha: 'b'.repeat(40) }), entry()]
  vi.spyOn(api, 'personalclawConfig').mockResolvedValue({
    durability: { auto_backup: true, time_travel: true },
  })
  vi.spyOn(api, 'durabilityStatus').mockResolvedValue({
    enabled: true,
    export: { last_run: 0, due_in_secs: 0, due: false },
    snapshot: { last_run: 0, due_in_secs: 0, due: false },
    drill: { last_run: 0, due_in_secs: 0, due: false },
    sync: {
      last_run: 0, due_in_secs: 0, due: false, enabled: false,
      transport: '', encrypt: 'auto', encrypted: false,
    },
  })
  vi.spyOn(api, 'durabilityArchive').mockResolvedValue({
    directory: '/tmp/snapshots', archives: [], would_prune: [],
    tiers: { daily: 14, weekly: 8, monthly: 12 },
    last_drill: { ran: false, ok: null, at: 0, detail: '', archive: '' },
  })
  vi.spyOn(api, 'settingsProviders').mockResolvedValue([])
  vi.spyOn(api, 'durabilityConflicts').mockResolvedValue({
    conflicts: [], truncated: false,
    counts: { total: 0, needs_review: 0, by_surface: {}, selected: 0 },
    surfaces: { memory: 'memory', knowledge: 'knowledge', durability: 'durability' },
    sync: { enabled: false, transport: '', configured: false },
  })
  vi.spyOn(api, 'durabilityHistory').mockResolvedValue({
    enabled: true,
    git: opts.git ?? true,
    dir: '/tmp/home/state-history',
    roots: [
      { id: 'config', label: 'Configuration', worktree: '/tmp/home', exists: true, commits: entries.length, memory: false },
      { id: 'memory', label: 'Memory', worktree: '/tmp/ws', exists: true, commits: 3, memory: true },
    ],
  })
  vi.spyOn(api, 'durabilityHistoryTimeline').mockImplementation(() =>
    opts.timelineError ? Promise.reject(opts.timelineError) : Promise.resolve(timeline(entries)),
  )
  previewCall = vi
    .spyOn(api, 'durabilityHistoryPreview')
    .mockResolvedValue({ confirmed: false, expected_head: 'b'.repeat(40), preview: opts.previewValue ?? preview() })
  applyCall = vi.spyOn(api, 'durabilityHistoryApply').mockResolvedValue({
    ok: true, operation: 'rollback', root: 'config', head: 'a'.repeat(40),
    prior_head: 'b'.repeat(40), prior_ref: 'refs/personalclaw/history/rollback-1-bbbbbbbb',
    reload_required: true,
  })
}

function mount() {
  return render(<><DurabilityPanel /><DialogHost /></>)
}

beforeEach(() => {
  invalidateKeys('settings:durability')
  invalidateKeys('settings:history')
  invalidateKeys('settings:history:config:all')
  invalidateKeys('settings:history:config:slept')
})

afterEach(() => {
  let pending: { id: number }[] = []
  subscribeDialogs((list) => { pending = list })()
  for (const d of pending) closeDialog(d.id, false)
  vi.restoreAllMocks()
})

describe('the timeline renders', () => {
  it('shows a recorded change — the vacuity floor for this file', async () => {
    stubPanel()
    mount()
    await waitFor(() => expect(screen.getAllByText('Configuration: 1 file changed').length).toBe(2))
    expect(screen.getByRole('button', { name: /see going back to here/i })).toBeTruthy()
    expect(screen.getAllByRole('button', { name: /see undoing just this/i }).length).toBe(2)
  })

  it('the newest change offers no "go back to here" — that would be a no-op', async () => {
    stubPanel()
    mount()
    await waitFor(() => expect(screen.getAllByText('Configuration: 1 file changed').length).toBe(2))
    // Two entries, one rollback button: the newest row is current state already.
    expect(screen.getAllByRole('button', { name: /see going back to here/i }).length).toBe(1)
  })

  it('a change made while nobody was watching says so', async () => {
    stubPanel({ entries: [entry({ subject: 'Memory: 3 files changed', surface: 'scheduled', unattended: true })] })
    mount()
    await waitFor(() => expect(screen.getByText('Memory: 3 files changed')).toBeTruthy())
    expect(screen.getByText(/while you were away/i)).toBeTruthy()
  })

  it('a FAILED read renders an error, not "nothing recorded"', async () => {
    stubPanel({ timelineError: new Error('history unreadable') })
    mount()
    await waitFor(() => expect(screen.getByText(/could not be read/i)).toBeTruthy())
    expect(screen.getByText(/history unreadable/)).toBeTruthy()
    expect(screen.queryByText(/Nothing recorded here yet/i)).toBeNull()
  })

  it('a machine with no git says nothing is being recorded', async () => {
    stubPanel({ git: false })
    mount()
    await waitFor(() => expect(screen.getByText(/needs/i)).toBeTruthy())
    expect(screen.getByText(/Nothing is being recorded/i)).toBeTruthy()
  })
})

describe('the preview gates the destructive call', () => {
  it('asking to see a rollback previews it and applies nothing', async () => {
    stubPanel()
    mount()
    await waitFor(() => expect(screen.getAllByText('Configuration: 1 file changed').length).toBe(2))
    screen.getByRole('button', { name: /see going back to here/i }).click()

    await waitFor(() => expect(screen.getByText('config.json')).toBeTruthy())
    expect(previewCall).toHaveBeenCalledWith('config', 'rollback', 'a'.repeat(40))
    // 🔁 Was `2 later change(s)`.
    expect(screen.getByText(/2 later changes would be set aside/i)).toBeTruthy()
    expect(screen.getByText(/"theme": "light"/)).toBeTruthy()
    expect(applyCall).not.toHaveBeenCalled()
  })

  it('a file too large to diff is LISTED with its size, never as an empty diff', async () => {
    stubPanel({
      previewValue: preview({
        files: [{ path: 'skills/big.md', status: 'M', bytes: 2_500_000, rendered: false, diff: '' }],
      }),
    })
    mount()
    await waitFor(() => expect(screen.getAllByText('Configuration: 1 file changed').length).toBe(2))
    screen.getByRole('button', { name: /see going back to here/i }).click()
    await waitFor(() => expect(screen.getByText('skills/big.md')).toBeTruthy())
    expect(screen.getByText(/too large to show here/i)).toBeTruthy()
    expect(screen.getByText(/2\.4 MB/)).toBeTruthy()
  })

  it('a DISMISSED confirmation applies nothing', async () => {
    stubPanel()
    mount()
    await waitFor(() => expect(screen.getAllByText('Configuration: 1 file changed').length).toBe(2))
    screen.getByRole('button', { name: /see going back to here/i }).click()
    await waitFor(() => expect(screen.getByText('config.json')).toBeTruthy())
    screen.getByRole('button', { name: /^roll back$/i }).click()
    const dialog = await screen.findByRole('alertdialog')
    const cancel = Array.from(dialog.querySelectorAll('button')).find((b) => /cancel/i.test(b.textContent ?? ''))
    cancel!.click()
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(applyCall).not.toHaveBeenCalled()
  })

  it('a confirmed rollback sends the head the PREVIEW returned', async () => {
    stubPanel()
    mount()
    await waitFor(() => expect(screen.getAllByText('Configuration: 1 file changed').length).toBe(2))
    screen.getByRole('button', { name: /see going back to here/i }).click()
    await waitFor(() => expect(screen.getByText('config.json')).toBeTruthy())
    screen.getByRole('button', { name: /^roll back$/i }).click()
    const dialog = await screen.findByRole('alertdialog')
    // The dialog names what is set aside and reassures about credentials — the two facts a
    // user needs before hard-resetting a tree they live in.
    const text = dialog.textContent ?? ''
    expect(text).toMatch(/2 changes made since are set aside/i)
    expect(text).toMatch(/credentials are untouched/i)
    const go = Array.from(dialog.querySelectorAll('button')).find((b) => /^roll back$/i.test(b.textContent?.trim() ?? ''))
    go!.click()
    // THE assertion of this file: the applied head is the previewed head, so the panel
    // physically cannot confirm without having previewed.
    await waitFor(() =>
      expect(applyCall).toHaveBeenCalledWith('config', 'rollback', 'a'.repeat(40), 'b'.repeat(40)),
    )
  })

  it('a revert is offered as its own verb, and previews as a revert', async () => {
    stubPanel({ previewValue: preview({ operation: 'revert', commits_rolled_away: 0 }) })
    mount()
    await waitFor(() => expect(screen.getAllByText('Configuration: 1 file changed').length).toBe(2))
    screen.getAllByRole('button', { name: /see undoing just this/i })[0].click()
    await waitFor(() => expect(screen.getByText('config.json')).toBeTruthy())
    expect(previewCall).toHaveBeenCalledWith('config', 'revert', 'b'.repeat(40))
    // Rollback and revert must not read the same: this one keeps later edits.
    expect(screen.getByText(/Later edits are kept/i)).toBeTruthy()
  })
})
