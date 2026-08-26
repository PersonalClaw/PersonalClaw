// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// ── PEP-5: the onboarding step that brings another local agent tool's setup over ──────────────
//
// PEP-4 shipped the engine; this step is the only thing a user can actually reach. So every
// assertion here is about the REAL call and the REAL answer, never about a helper:
//
//   · the step SCANS on mount and IMPORTS only when asked — a first-run screen that wrote to
//     the home on render would be a side effect of looking;
//   · the POST carries the two SELECTION axes and nothing else. An `ImportItem` holds a
//     filesystem path, so a payload that echoed items back would be a way to have any
//     directory copied into the home. The vacuity assertion proves the fixture DOES contain
//     the path the payload must not carry;
//   · a failed POST shows the gateway's own sentence in a `role="alert"` and does NOT advance;
//   · `conflict` and `rejected` rows are RENDERED, with the writer's reason. Those are the
//     outcomes a "4 imported" headline would hide, and a conflict silently swallowed is a
//     write the user believes happened;
//   · items the importer already wrote come back `existing` and are shown as already imported,
//     which is what makes a second visit legible instead of a duplicate-import trap.

const onboardingImportScan = vi.fn()
const runOnboardingImport = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    onboardingImportScan: () => onboardingImportScan(),
    runOnboardingImport: (...a: unknown[]) => runOnboardingImport(...a),
  },
}))

import { ImportStep, summaryOfReport } from './ImportStep'
import type { OnboardingImportReport, OnboardingImportScan } from '../../lib/api'

const onDone = vi.fn()
const onSkip = vi.fn()

const ZERO = { instructions: 0, memories: 0, mcp_servers: 0, skills: 0, settings: 0 }
const CATEGORIES = ['instructions', 'memories', 'mcp_servers', 'skills', 'settings']

/** A scan shaped like the gateway's, with a root path and an item key the POST must not carry. */
const CLAUDE_ROOT = '/home/ada/.claude'
function scan(overrides: Partial<OnboardingImportScan['sources'][number]> = {}): OnboardingImportScan {
  return {
    categories: CATEGORIES,
    sources: [
      {
        source: 'claude_code', display_name: 'Claude Code', root: CLAUDE_ROOT,
        present: true, detected: true,
        counts: { ...ZERO, instructions: 1, mcp_servers: 2, skills: 1 },
        items: [
          { fingerprint: 'f1', source: 'claude_code', category: 'instructions', key: 'CLAUDE.md', title: 'CLAUDE.md', redactions: 1, existing: false },
          { fingerprint: 'f2', source: 'claude_code', category: 'mcp_servers', key: 'weather', title: 'weather', redactions: 0, existing: false },
          { fingerprint: 'f3', source: 'claude_code', category: 'mcp_servers', key: 'github', title: 'github', redactions: 0, existing: false },
          { fingerprint: 'f4', source: 'claude_code', category: 'skills', key: 'tidy-notes', title: 'tidy-notes', redactions: 0, existing: false },
        ],
        secrets_skipped: 2, redactions: 1,
        notes: ['2 credential value(s) or file(s) were skipped and not imported.'],
        ...overrides,
      },
      {
        source: 'codex', display_name: 'Codex', root: '/home/ada/.codex',
        present: false, detected: false, counts: { ...ZERO }, items: [],
        secrets_skipped: 0, redactions: 0, notes: [],
      },
    ],
  }
}

function report(rows: OnboardingImportReport['results'], extra: Partial<OnboardingImportReport> = {}): OnboardingImportReport {
  const counts = { imported: 0, existing: 0, conflict: 0, rejected: 0 }
  for (const r of rows) counts[r.outcome] += 1
  return { counts, results: rows, secrets_skipped: 0, redactions: 0, notes: [], ...extra }
}

const IMPORTED_ROW = {
  fingerprint: 'f2', source: 'claude_code', category: 'mcp_servers', key: 'weather',
  outcome: 'imported' as const, destination: 'mcp.json', detail: '',
}

beforeEach(() => {
  vi.clearAllMocks()
  onboardingImportScan.mockResolvedValue(scan())
  runOnboardingImport.mockResolvedValue(report([IMPORTED_ROW]))
})

function mount() {
  render(<ImportStep onDone={onDone} onSkip={onSkip} />)
}

/** Mount and wait for the scan to land, so no assertion races the fetch. */
async function mounted() {
  mount()
  await waitFor(() => expect(onboardingImportScan).toHaveBeenCalled())
  return screen.findByText('Claude Code')
}

// ── what the scan surfaces ────────────────────────────────────────────────────


describe('the step scans on mount and offers what it found', () => {
  it('names the detected tool, where it lives, and how much it holds', async () => {
    await mounted()
    expect(screen.getByText(CLAUDE_ROOT)).toBeTruthy()
    expect(screen.getByText('4 things found')).toBeTruthy()
    // A tool that is not installed is not offered as something to import from.
    expect(screen.queryByText('Codex')).toBeNull()
  })

  it('offers a checkbox per category that HAS something, with its count', async () => {
    await mounted()
    expect(screen.getByRole('checkbox', { name: 'Bring over Instructions (1)' })).toBeTruthy()
    expect(screen.getByRole('checkbox', { name: 'Bring over MCP servers (2)' })).toBeTruthy()
    expect(screen.getByRole('checkbox', { name: 'Bring over Skills (1)' })).toBeTruthy()
    // Empty categories are not rendered as ticked boxes that would import nothing.
    expect(screen.queryByRole('checkbox', { name: /Memories/ })).toBeNull()
    expect(screen.queryByRole('checkbox', { name: /Settings/ })).toBeNull()
  })

  it('says how many credentials will be withheld — a count, never a value', async () => {
    await mounted()
    expect(screen.getByText(/2 credential value\(s\) or file\(s\) will not be imported/)).toBeTruthy()
  })

  it('imports NOTHING on mount', async () => {
    await mounted()
    expect(runOnboardingImport).not.toHaveBeenCalled()
  })

  it('a scan that fails says so and offers a retry', async () => {
    onboardingImportScan.mockRejectedValue(new Error('the foreign root is unreadable'))
    mount()
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText(/the foreign root is unreadable/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(onboardingImportScan).toHaveBeenCalledTimes(2))
  })
})

// ── what the POST carries ─────────────────────────────────────────────────────


describe('the import posts the two selection axes, and only those', () => {
  it('sends the picked source and every offered category', async () => {
    await mounted()
    fireEvent.click(screen.getByRole('button', { name: /Import selected/ }))
    await waitFor(() => expect(runOnboardingImport).toHaveBeenCalledWith({
      sources: ['claude_code'],
      categories: ['instructions', 'mcp_servers', 'skills'],
    }))
  })

  it('never sends an item, a title or a filesystem path', async () => {
    await mounted()
    fireEvent.click(screen.getByRole('button', { name: /Import selected/ }))
    await waitFor(() => expect(runOnboardingImport).toHaveBeenCalled())
    const sent = JSON.stringify(runOnboardingImport.mock.calls[0][0])
    // Vacuity: the SCAN carries both, so a payload that echoed the scan back would fail here.
    expect(JSON.stringify(scan())).toContain(CLAUDE_ROOT)
    expect(JSON.stringify(scan())).toContain('CLAUDE.md')
    expect(sent).not.toContain(CLAUDE_ROOT)
    expect(sent).not.toContain('CLAUDE.md')
    expect(sent).not.toContain('fingerprint')
  })

  it('un-ticking a category drops it from the request', async () => {
    await mounted()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Bring over Skills (1)' }))
    fireEvent.click(screen.getByRole('button', { name: /Import selected/ }))
    await waitFor(() => expect(runOnboardingImport).toHaveBeenCalledWith({
      sources: ['claude_code'],
      categories: ['instructions', 'mcp_servers'],
    }))
  })

  it('un-ticking the only tool makes Import unavailable, and says why', async () => {
    await mounted()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Import from Claude Code' }))
    const button = screen.getByRole('button', { name: /Import selected/ })
    expect(button.getAttribute('title')).toMatch(/Pick a tool/)
    fireEvent.click(button)
    expect(runOnboardingImport).not.toHaveBeenCalled()
  })
})

// ── failures are shown, never swallowed ───────────────────────────────────────


describe('a failed import is reported on the surface that caused it', () => {
  it("shows the gateway's own sentence and does not advance", async () => {
    runOnboardingImport.mockRejectedValue(new Error('The import stopped after a write failed: read-only file system.'))
    await mounted()
    fireEvent.click(screen.getByRole('button', { name: /Import selected/ }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('read-only file system')
    // The step stays put: a failure that advanced would look exactly like a success.
    expect(onDone).not.toHaveBeenCalled()
    // And it offers the retry, which is safe because the ledger recorded what landed.
    expect(screen.getByRole('button', { name: /Try again/ })).toBeTruthy()
  })

  it('a CONFLICT is listed with the writer\'s reason, not hidden behind a count', async () => {
    runOnboardingImport.mockResolvedValue(report([
      IMPORTED_ROW,
      {
        fingerprint: 'f3', source: 'claude_code', category: 'mcp_servers', key: 'github',
        outcome: 'conflict', destination: 'mcp.json',
        detail: "mcp server 'github' is already configured with a different command",
      },
    ]))
    await mounted()
    fireEvent.click(screen.getByRole('button', { name: /Import selected/ }))
    expect(await screen.findByRole('group', { name: 'Kept what you already had' })).toBeTruthy()
    expect(screen.getByText(/already configured with a different command/)).toBeTruthy()
    // The collapsed row will carry it too, so it survives the user moving on.
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }))
    expect(onDone).toHaveBeenCalledWith('1 imported · 1 to review')
  })

  it('a REJECTED item is listed too — a security refusal is not a silent skip', async () => {
    runOnboardingImport.mockResolvedValue(report([
      {
        fingerprint: 'f4', source: 'claude_code', category: 'skills', key: 'tidy-notes',
        outcome: 'rejected', destination: '', detail: 'the skill install scan refused it',
      },
    ]))
    await mounted()
    fireEvent.click(screen.getByRole('button', { name: /Import selected/ }))
    expect(await screen.findByRole('group', { name: 'Refused for safety' })).toBeTruthy()
    expect(screen.getByText(/the skill install scan refused it/)).toBeTruthy()
  })
})

// ── the report, and re-entry ───────────────────────────────────────────────────


describe('the report says what happened, out loud', () => {
  it('renders each imported item with its destination', async () => {
    await mounted()
    fireEvent.click(screen.getByRole('button', { name: /Import selected/ }))
    expect(await screen.findByText('weather → mcp.json')).toBeTruthy()
  })

  it('announces the outcome in a polite live region', async () => {
    await mounted()
    fireEvent.click(screen.getByRole('button', { name: /Import selected/ }))
    const live = await screen.findByText('Import finished: 1 imported.')
    expect(live.getAttribute('aria-live')).toBe('polite')
    expect(live.className).toContain('sr-only')
  })

  it('repeats the withheld-credential count from the report', async () => {
    runOnboardingImport.mockResolvedValue(report([IMPORTED_ROW], {
      secrets_skipped: 2, notes: ['2 credential value(s) or file(s) were skipped and not imported.'],
    }))
    await mounted()
    fireEvent.click(screen.getByRole('button', { name: /Import selected/ }))
    expect(await screen.findByText(/2 credential value\(s\) or file\(s\) were skipped/)).toBeTruthy()
  })
})

describe('re-entry is legible, not a duplicate-import trap', () => {
  it("marks what the importer already wrote as 'already imported'", async () => {
    const s = scan()
    s.sources[0].items = s.sources[0].items.map((i) =>
      i.category === 'mcp_servers' ? { ...i, existing: true } : i)
    onboardingImportScan.mockResolvedValue(s)
    await mounted()
    expect(screen.getByText('2 already imported')).toBeTruthy()
    // Only the imported category says so — a blanket badge would be just as wrong.
    expect(screen.getAllByText(/already imported/)).toHaveLength(1)
  })
})

describe('a machine with no other agent tool', () => {
  it('says so, names what it looked for, and continues', async () => {
    const s = scan()
    s.sources = s.sources.map((x) => ({ ...x, present: false, detected: false, items: [] }))
    onboardingImportScan.mockResolvedValue(s)
    mount()
    expect(await screen.findByText(/No other agent tools found on this machine/)).toBeTruthy()
    // "we found nothing" is only trustworthy if you know where it looked.
    expect(screen.getByText(/Claude Code and Codex/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }))
    expect(onDone).toHaveBeenCalledWith('Nothing to import')
  })
})

describe('skipping is free', () => {
  it('the skip link leaves without importing', async () => {
    await mounted()
    fireEvent.click(screen.getByRole('button', { name: 'Skip this' }))
    expect(onSkip).toHaveBeenCalled()
    expect(runOnboardingImport).not.toHaveBeenCalled()
  })
})

describe('summaryOfReport names every non-zero outcome', () => {
  it('so nothing disappears when the step collapses', () => {
    expect(summaryOfReport(report([]))).toBe('Nothing to import')
    expect(summaryOfReport(report([
      IMPORTED_ROW,
      { ...IMPORTED_ROW, fingerprint: 'x', outcome: 'existing' },
      { ...IMPORTED_ROW, fingerprint: 'y', outcome: 'conflict' },
      { ...IMPORTED_ROW, fingerprint: 'z', outcome: 'rejected' },
    ]))).toBe('1 imported · 1 already there · 1 to review · 1 refused')
  })
})
