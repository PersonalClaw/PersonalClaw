// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'

// ── PEP-5: the onboarding step that brings another local agent tool's setup over ──────────────
//
// The owner's ask, verbatim: the step must "show you a list of things that you're bringing over
// along with the count and allow you to choose what to bring over and what not to. It should be
// collapsed into a number and a checkbox by default but the user should be able to expand and pick
// and choose if they want to." So every assertion here is about what a user SEES and what the REAL
// call CARRIES, never about a helper:
//
//   · collapsed by default: one ticked box per group, with its count, and no item rows;
//   · a real disclosure opens the group's items, each with its own box;
//   · the group box is DERIVED — unticking one item makes it mixed and the count "n-1 of n", a
//     click on the mixed box chooses them all, unticking it unticks them all;
//   · the POST carries exactly the chosen FINGERPRINTS and nothing else. An item holds a filesystem
//     path, so a payload that echoed items back would be a way to have any directory copied into
//     the home; the vacuity assertions prove the fixture DOES hold what the payload must not carry;
//   · an item importing would not write (already here, a conflict) is listed with its reason and
//     has NO box — a box there would promise a write no choice can cause;
//   · the report says what came over, what was LEFT OUT by choice, what conflicted, and what was
//     gone by the time the import ran.

const onboardingImportScan = vi.fn()
const runOnboardingImport = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    onboardingImportScan: () => onboardingImportScan(),
    runOnboardingImport: (...a: unknown[]) => runOnboardingImport(...a),
  },
}))

import { ImportStep, summaryOfReport } from './ImportStep'
import type {
  OnboardingImportItem, OnboardingImportReport, OnboardingImportScan,
} from '../../lib/api'

const onDone = vi.fn()
const onSkip = vi.fn()

const ZERO = { instructions: 0, memories: 0, mcp_servers: 0, skills: 0, settings: 0 }
const CATEGORIES = ['instructions', 'memories', 'mcp_servers', 'skills', 'settings']

/** An item shaped like the gateway's, new unless told otherwise. */
function item(fingerprint: string, category: string, key: string, extra: Partial<OnboardingImportItem> = {}): OnboardingImportItem {
  return {
    fingerprint, source: 'claude_code', category, key, title: key,
    state: 'new', destination: `dest/${key}`, detail: '', secrets_skipped: 0, redactions: 0,
    ...extra,
  }
}

/** A scan shaped like the gateway's, with a root path and item keys the POST must not carry. */
const CLAUDE_ROOT = '/home/ada/.claude'
const ITEMS = () => [
  item('f1', 'instructions', 'CLAUDE.md', { redactions: 1 }),
  item('f2', 'mcp_servers', 'weather', { secrets_skipped: 1 }),
  item('f3', 'mcp_servers', 'github'),
  item('f4', 'skills', 'tidy-notes'),
]
function scan(items: OnboardingImportItem[] = ITEMS(), overrides: Partial<OnboardingImportScan['sources'][number]> = {}): OnboardingImportScan {
  return {
    categories: CATEGORIES,
    sources: [
      {
        source: 'claude_code', display_name: 'Claude Code', root: CLAUDE_ROOT,
        present: true, detected: true,
        counts: { ...ZERO, instructions: 1, mcp_servers: 2, skills: 1 },
        items,
        secrets_skipped: 2, redactions: 1,
        notes: ['2 credential values or files were skipped and not imported.'],
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
  return { counts, results: rows, unselected: [], missing: [], secrets_skipped: 0, redactions: 0, notes: [], ...extra }
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

const groupBox = (label: string) =>
  screen.getByRole('checkbox', { name: new RegExp(`^Bring over ${label} from Claude Code`) }) as HTMLInputElement
/** The group's disclosure — "Choose" while it has something to choose, "Show" when it has not. */
const disclosure = (label: string) =>
  screen.getByRole('button', { name: new RegExp(`^(Choose|Show) ${label} from Claude Code$`) })
/** The visible text of a group's row: its name, its count and what is already settled. */
const rowText = (label: string) => disclosure(label).parentElement!.textContent ?? ''
const box = (name: string) => screen.getByRole('checkbox', { name: new RegExp(`^${name}\\b`) }) as HTMLInputElement

function importNow() {
  fireEvent.click(screen.getByRole('button', { name: /^Import/ }))
}

// ── collapsed by default ──────────────────────────────────────────────────────


describe('collapsed by default: a count and a ticked box per group', () => {
  it('names the detected tool, where it lives, and how much it holds', async () => {
    await mounted()
    expect(screen.getByText(CLAUDE_ROOT)).toBeTruthy()
    expect(screen.getByText('4 things found')).toBeTruthy()
    // A tool that is not installed is not offered as something to import from.
    expect(screen.queryByText('Codex')).toBeNull()
  })

  it('shows each group as ONE ticked row with its count, and no item rows at all', async () => {
    await mounted()
    for (const [label, n] of [['Instructions', 1], ['MCP servers', 2], ['Skills', 1]] as const) {
      const tick = groupBox(label)
      expect(tick.checked, `${label} starts ticked — the user came to bring their setup over`).toBe(true)
      expect(tick.indeterminate).toBe(false)
      expect(tick.getAttribute('aria-label')).toBe(`Bring over ${label} from Claude Code, ${n}`)
      const toggle = disclosure(label)
      expect(toggle.getAttribute('aria-expanded')).toBe('false')
      expect(toggle.textContent).toContain('Choose')
      expect(rowText(label)).toContain(`${label}· ${n}`)
    }
    // Where each group lands is on the row, so a tick is an informed choice.
    expect(screen.getByText('MCP server definitions, added to your MCP config.')).toBeTruthy()
    // Collapsed means collapsed: no item is rendered until a group is opened.
    expect(screen.queryByRole('checkbox', { name: /^weather/ })).toBeNull()
    expect(screen.queryByRole('list')).toBeNull()
    // Empty categories are not rendered as ticked boxes that would import nothing.
    expect(screen.queryByRole('checkbox', { name: /Memories/ })).toBeNull()
    expect(screen.queryByRole('checkbox', { name: /Settings/ })).toBeNull()
    // …and the primary action states the total it will bring over.
    expect(screen.getByRole('button', { name: /Import 4 items/ })).toBeTruthy()
  })

  it('names the tools it found, in a sentence that agrees with how many there are', async () => {
    await mounted()
    expect(screen.getByText(/^We found Claude Code on this machine\. Bring its setup over — it is only read/)).toBeTruthy()
  })

  it('and with two tools, says "their" — not "another agent tool"', async () => {
    const s = scan()
    s.sources[1] = { ...s.sources[1], present: true, detected: true, items: [item('c1', 'instructions', 'AGENTS.md', { source: 'codex' })] }
    onboardingImportScan.mockResolvedValue(s)
    await mounted()
    expect(screen.getByText(/^We found Claude Code and Codex on this machine\. Bring their setup over — they are only read/)).toBeTruthy()
    // Each tool frames its own groups, so the same category reads once per tool.
    expect(screen.getByRole('checkbox', { name: 'Bring over Instructions from Codex, 1' })).toBeTruthy()
  })

  it('says how many credentials will be withheld — a count, never a value', async () => {
    await mounted()
    expect(screen.getByText(/2 credential values or files will not be imported/)).toBeTruthy()
  })

  it('and reads SINGULAR when exactly one credential is withheld', async () => {
    onboardingImportScan.mockResolvedValue(scan(ITEMS(), { secrets_skipped: 1 }))
    await mounted()
    expect(screen.getByText(/1 credential value or file will not be imported/)).toBeTruthy()
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

// ── expanding ─────────────────────────────────────────────────────────────────


describe('expanding a group lists every item, each with its own box', () => {
  it('opens through a real disclosure wired to the list it controls', async () => {
    await mounted()
    const toggle = disclosure('MCP servers')
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    const list = document.getElementById(toggle.getAttribute('aria-controls')!)!
    expect(list, 'aria-controls names the list it opened').toBeTruthy()
    expect(list.getAttribute('aria-label')).toBe('MCP servers from Claude Code')
    const rows = within(list).getAllByRole('listitem')
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('weather'), expect.stringContaining('github'),
    ])
    expect(box('weather').checked).toBe(true)
    expect(box('github').checked).toBe(true)
    expect(within(list).getAllByText('New')).toHaveLength(2)
    // Each item says what was left OUT of it — so the user knows which server needs its key again.
    expect(within(rows[0]).getByText('1 credential left out')).toBeTruthy()
    expect(box('weather').getAttribute('aria-label')).toBe('weather, 1 credential left out')
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('checkbox', { name: /^weather/ })).toBeNull()
  })
})

// ── the group box follows its items ───────────────────────────────────────────


describe('the group box is tri-state, and derived from the items it stands for', () => {
  it('unticking one item makes the group MIXED and its count "n-1 of n"', async () => {
    await mounted()
    fireEvent.click(disclosure('MCP servers'))
    fireEvent.click(box('weather'))
    const tick = groupBox('MCP servers')
    expect(tick.indeterminate, 'partly chosen is neither ticked nor clear').toBe(true)
    expect(tick.checked).toBe(false)
    expect(tick.getAttribute('aria-label')).toBe('Bring over MCP servers from Claude Code, 1 of 2')
    expect(rowText('MCP servers')).toContain('· 1 of 2')
    // The total on the action moves with it.
    expect(screen.getByRole('button', { name: /Import 3 items/ })).toBeTruthy()
  })

  it('a click on the MIXED box chooses the whole group again', async () => {
    await mounted()
    fireEvent.click(disclosure('MCP servers'))
    fireEvent.click(box('weather'))
    fireEvent.click(groupBox('MCP servers'))
    expect(groupBox('MCP servers').checked).toBe(true)
    expect(groupBox('MCP servers').indeterminate).toBe(false)
    expect(box('weather').checked).toBe(true)
  })

  it('unticking the group unticks every item in it', async () => {
    await mounted()
    fireEvent.click(disclosure('MCP servers'))
    fireEvent.click(groupBox('MCP servers'))
    expect(box('weather').checked).toBe(false)
    expect(box('github').checked).toBe(false)
    expect(groupBox('MCP servers').indeterminate).toBe(false)
    expect(groupBox('MCP servers').getAttribute('aria-label')).toBe('Bring over MCP servers from Claude Code, 0 of 2')
    // A collapsed group is changed the same way, without opening it.
    fireEvent.click(groupBox('Skills'))
    expect(screen.getByRole('button', { name: /Import 1 item\b/ })).toBeTruthy()
  })
})

// ── what the POST carries ─────────────────────────────────────────────────────


describe('the import sends exactly the chosen fingerprints, and nothing else', () => {
  it('sends every item by default, in scan order', async () => {
    await mounted()
    importNow()
    await waitFor(() => expect(runOnboardingImport).toHaveBeenCalledWith({ fingerprints: ['f1', 'f2', 'f3', 'f4'] }))
  })

  it('an unticked item, and an unticked group, are left out of the request', async () => {
    await mounted()
    fireEvent.click(disclosure('MCP servers'))
    fireEvent.click(box('weather'))
    fireEvent.click(groupBox('Skills'))
    importNow()
    await waitFor(() => expect(runOnboardingImport).toHaveBeenCalledWith({ fingerprints: ['f1', 'f3'] }))
  })

  it('never sends an item, a title, a key or a filesystem path', async () => {
    await mounted()
    importNow()
    await waitFor(() => expect(runOnboardingImport).toHaveBeenCalled())
    const sent = runOnboardingImport.mock.calls[0][0]
    expect(Object.keys(sent)).toEqual(['fingerprints'])
    const wire = JSON.stringify(sent)
    // Vacuity: the SCAN carries all of these, so a payload that echoed it back would fail here.
    const scanned = JSON.stringify(scan())
    for (const leak of [CLAUDE_ROOT, 'CLAUDE.md', 'weather', 'dest/']) {
      expect(scanned).toContain(leak)
      expect(wire).not.toContain(leak)
    }
  })

  it('un-ticking everything makes Import unavailable, and says why', async () => {
    await mounted()
    for (const label of ['Instructions', 'MCP servers', 'Skills']) fireEvent.click(groupBox(label))
    const button = screen.getByRole('button', { name: /Import selected/ })
    expect(button.getAttribute('title')).toMatch(/Pick at least one thing/)
    fireEvent.click(button)
    expect(runOnboardingImport).not.toHaveBeenCalled()
  })
})

// ── what a choice cannot change ───────────────────────────────────────────────


describe('an item importing would not write is listed with its reason, and has no box', () => {
  function settledScan() {
    return scan([
      item('f1', 'instructions', 'CLAUDE.md'),
      item('f2', 'mcp_servers', 'weather', {
        state: 'conflict', destination: 'mcp.json#mcpServers.weather',
        detail: 'an MCP server of this name is already configured differently, and it is kept',
      }),
      item('f3', 'mcp_servers', 'github'),
      item('f4', 'skills', 'tidy-notes', { state: 'existing', detail: 'already imported' }),
    ])
  }

  it('a group counts only what can still come over, and says what is already settled', async () => {
    onboardingImportScan.mockResolvedValue(settledScan())
    await mounted()
    expect(groupBox('MCP servers').getAttribute('aria-label')).toBe('Bring over MCP servers from Claude Code, 1')
    expect(rowText('MCP servers')).toContain('· 1 conflict')
    // Everything in Skills is already here: no box to tick, the row says why, and its disclosure
    // offers to SHOW rather than to choose — there is nothing in it to choose.
    expect(screen.queryByRole('checkbox', { name: /^Bring over Skills/ })).toBeNull()
    expect(rowText('Skills')).toContain('· 1 already here')
    expect(disclosure('Skills').textContent).toContain('Show')

    fireEvent.click(disclosure('MCP servers'))
    expect(screen.queryByRole('checkbox', { name: /^weather/ }), 'a conflict is never a choice').toBeNull()
    const list = screen.getByRole('list', { name: 'MCP servers from Claude Code' })
    expect(within(list).getByText('Conflict')).toBeTruthy()
    expect(within(list).getByText('An MCP server of this name is already configured differently, and it is kept.')).toBeTruthy()

    importNow()
    await waitFor(() => expect(runOnboardingImport).toHaveBeenCalledWith({ fingerprints: ['f1', 'f3'] }))
  })

  it('when nothing at all is new, the step moves on instead of offering an empty import', async () => {
    onboardingImportScan.mockResolvedValue(scan(ITEMS().map((i) => ({ ...i, state: 'existing' as const }))))
    await mounted()
    expect(screen.queryByRole('button', { name: /^Import/ })).toBeNull()
    expect(screen.getByText(/nothing new to bring over/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }))
    expect(onDone).toHaveBeenCalledWith('4 already here')
    expect(runOnboardingImport).not.toHaveBeenCalled()
  })
})

// ── large groups ──────────────────────────────────────────────────────────────


describe('a group of hundreds stays usable', () => {
  const MANY = Array.from({ length: 120 }, (_, n) => item(`s${n}`, 'skills', `skill-${n}`))

  it('pages its rows, discloses the rest, and a filter narrows it', async () => {
    onboardingImportScan.mockResolvedValue(scan(MANY))
    await mounted()
    expect(groupBox('Skills').getAttribute('aria-label')).toBe('Bring over Skills from Claude Code, 120')
    fireEvent.click(disclosure('Skills'))
    const list = screen.getByRole('list', { name: 'Skills from Claude Code' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(50)
    const more = screen.getByRole('button', { name: 'Show 50 more (70 not shown)' })

    fireEvent.click(more)
    expect(within(list).getAllByRole('listitem')).toHaveLength(100)
    // Focus lands on the first row it revealed — not on the document when the button goes.
    expect(document.activeElement).toBe(box('skill-50'))
    fireEvent.click(screen.getByRole('button', { name: 'Show 20 more (20 not shown)' }))
    expect(within(list).getAllByRole('listitem')).toHaveLength(120)
    expect(screen.queryByRole('button', { name: /Show \d+ more/ })).toBeNull()
    expect(document.activeElement).toBe(box('skill-100'))

    fireEvent.change(screen.getByRole('searchbox', { name: 'Filter Skills from Claude Code' }), { target: { value: 'skill-11' } })
    expect(within(list).getAllByRole('listitem')).toHaveLength(11) // skill-11, skill-110…119
    expect(screen.getByText('11 of 120 match')).toBeTruthy()
    // Said out of a live region too, so a screen reader hears the filter work without leaving the box.
    expect(screen.getByText('11 items').getAttribute('role')).toBe('status')
  })

  it('the group box still stands for ALL its items while a filter hides some', async () => {
    onboardingImportScan.mockResolvedValue(scan(MANY))
    await mounted()
    fireEvent.click(disclosure('Skills'))
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'skill-7' } })
    fireEvent.click(groupBox('Skills'))
    expect(groupBox('Skills').getAttribute('aria-label')).toBe('Bring over Skills from Claude Code, 0 of 120')
  })
})

// ── failures are shown, never swallowed ───────────────────────────────────────


describe('a failed import is reported on the surface that caused it', () => {
  it("shows the gateway's own sentence and does not advance", async () => {
    runOnboardingImport.mockRejectedValue(new Error('The import stopped after a write failed: read-only file system.'))
    await mounted()
    importNow()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('read-only file system')
    // The step stays put: a failure that advanced would look exactly like a success.
    expect(onDone).not.toHaveBeenCalled()
    // And it offers the retry, which is safe because the ledger recorded what landed.
    expect(screen.getByRole('button', { name: /Try again/ })).toBeTruthy()
  })
})

// ── the report ────────────────────────────────────────────────────────────────


describe('the report says what came over, what was left out, and what conflicted', () => {
  it('says what landed as a count per group, and each item with its destination on request', async () => {
    await mounted()
    importNow()
    const landed = await screen.findByRole('group', { name: 'Brought over' })
    expect(within(landed).getByText('MCP servers · 1')).toBeTruthy()
    expect(screen.queryByText('weather → mcp.json'), 'collapsed to a number until asked').toBeNull()
    const more = within(landed).getByRole('button', { name: 'Show each item' })
    expect(more.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(more)
    expect(more.getAttribute('aria-expanded')).toBe('true')
    expect(document.getElementById(more.getAttribute('aria-controls')!)?.textContent).toContain('weather → mcp.json')
  })

  it('what needs attention comes BEFORE what landed', async () => {
    // Driven with a 76-item import, the conflicts and the Continue button sat below 76 rows of success.
    runOnboardingImport.mockResolvedValue(report([IMPORTED_ROW], {
      unselected: [item('f9', 'skills', 'mine', { state: 'conflict', detail: 'kept' }), item('f4', 'skills', 'tidy-notes')],
    }))
    await mounted()
    importNow()
    const landed = await screen.findByRole('group', { name: 'Brought over' })
    for (const name of ['Kept what you already had', 'Left out, as you chose']) {
      const earlier = screen.getByRole('group', { name })
      expect(earlier.compareDocumentPosition(landed) & Node.DOCUMENT_POSITION_FOLLOWING, `${name} precedes it`).toBeTruthy()
    }
  })

  it('lists what the user LEFT OUT, and the collapsed row carries it', async () => {
    runOnboardingImport.mockResolvedValue(report([IMPORTED_ROW], {
      unselected: [item('f4', 'skills', 'tidy-notes'), item('f1', 'instructions', 'CLAUDE.md')],
    }))
    await mounted()
    importNow()
    const left = await screen.findByRole('group', { name: 'Left out, as you chose' })
    expect(within(left).getByText('Skills · tidy-notes')).toBeTruthy()
    expect(within(left).getByText('Instructions · CLAUDE.md')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }))
    expect(onDone).toHaveBeenCalledWith('1 imported · 2 left out')
  })

  it('a CONFLICT is listed with the writer\'s reason — whether it was picked or not', async () => {
    runOnboardingImport.mockResolvedValue(report([
      IMPORTED_ROW,
      {
        fingerprint: 'f3', source: 'claude_code', category: 'mcp_servers', key: 'github',
        outcome: 'conflict', destination: 'mcp.json',
        detail: 'an MCP server of this name is already configured differently, and it is kept',
      },
    ], {
      unselected: [item('f9', 'skills', 'mine', { state: 'conflict', detail: 'a skill of this name that no import wrote is already here, and it is kept' })],
    }))
    await mounted()
    importNow()
    const kept = await screen.findByRole('group', { name: 'Kept what you already had' })
    expect(within(kept).getByText(/already configured differently/)).toBeTruthy()
    expect(within(kept).getByText(/no import wrote/)).toBeTruthy()
    // The collapsed row will carry it too, so it survives the user moving on.
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }))
    expect(onDone).toHaveBeenCalledWith('1 imported · 2 to review')
  })

  it('a REJECTED item is listed too — a security refusal is not a silent skip', async () => {
    runOnboardingImport.mockResolvedValue(report([
      {
        fingerprint: 'f4', source: 'claude_code', category: 'skills', key: 'tidy-notes',
        outcome: 'rejected', destination: '', detail: 'the skill install scan refused it',
      },
    ]))
    await mounted()
    importNow()
    expect(await screen.findByRole('group', { name: 'Refused for safety' })).toBeTruthy()
    expect(screen.getByText(/the skill install scan refused it/)).toBeTruthy()
  })

  it('a pick that was gone by the time the import ran is named, not dropped', async () => {
    runOnboardingImport.mockResolvedValue(report([IMPORTED_ROW], { missing: ['f1'] }))
    await mounted()
    importNow()
    const gone = await screen.findByRole('group', { name: 'No longer there' })
    // By NAME, from the scan the user picked it in — the server only knows the fingerprint.
    expect(gone.textContent).toContain('CLAUDE.md')
    expect(gone.textContent).toContain('nothing was brought over for it')
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }))
    expect(onDone).toHaveBeenCalledWith('1 imported · 1 no longer found')
  })

  it('announces the outcome in a polite live region', async () => {
    await mounted()
    importNow()
    const live = await screen.findByText('Import finished: 1 imported.')
    expect(live.getAttribute('aria-live')).toBe('polite')
    expect(live.className).toContain('sr-only')
  })

  it('repeats the withheld-credential count from the report', async () => {
    runOnboardingImport.mockResolvedValue(report([IMPORTED_ROW], {
      secrets_skipped: 2, notes: ['2 credential values or files were skipped and not imported.'],
    }))
    await mounted()
    importNow()
    expect(await screen.findByText(/2 credential values or files were skipped/)).toBeTruthy()
    expect(screen.queryByText(/value\(s\)|file\(s\)/)).toBeNull()
  })

  it('and the backend note reads SINGULAR at exactly one withheld credential', async () => {
    runOnboardingImport.mockResolvedValue(report([IMPORTED_ROW], {
      secrets_skipped: 1, notes: ['1 credential value or file was skipped and not imported.'],
    }))
    await mounted()
    importNow()
    expect(await screen.findByText(/1 credential value or file was skipped/)).toBeTruthy()
    expect(screen.queryByText(/values or files|were skipped/)).toBeNull()
  })
})

describe('re-entry is legible, not a duplicate-import trap', () => {
  it("shows what the importer already wrote as already here, on its group and its rows", async () => {
    onboardingImportScan.mockResolvedValue(scan(ITEMS().map((i) =>
      i.category === 'mcp_servers' ? { ...i, state: 'existing' as const, detail: 'already configured identically' } : i)))
    await mounted()
    expect(rowText('MCP servers')).toContain('· 2 already here')
    // Only the imported group says so — a blanket badge would be just as wrong.
    expect(screen.getAllByText(/already here/)).toHaveLength(1)
    fireEvent.click(disclosure('MCP servers'))
    expect(screen.getAllByText('Already here')).toHaveLength(2)
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

  it('🔴 and it survives a FAILED scan — an optional step must not become a wall', async () => {
    // Measured with the gateway dead: the error branch was an early `return <LoadError …/>` that
    // replaced the whole body, taking the skip link with it. `EssentialsStep`'s catalog-error
    // branch always kept its escape; this is the same shape.
    onboardingImportScan.mockRejectedValue(new Error('gateway down'))
    mount()
    expect(await screen.findByRole('button', { name: /Retry/ })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Skip this' }))
    expect(onSkip).toHaveBeenCalled()
    expect(runOnboardingImport).not.toHaveBeenCalled()
  })
})

describe('summaryOfReport names every non-zero outcome, the choice included', () => {
  it('so nothing disappears when the step collapses', () => {
    expect(summaryOfReport(report([]))).toBe('Nothing to import')
    expect(summaryOfReport(report([
      IMPORTED_ROW,
      { ...IMPORTED_ROW, fingerprint: 'x', outcome: 'existing' },
      { ...IMPORTED_ROW, fingerprint: 'y', outcome: 'conflict' },
      { ...IMPORTED_ROW, fingerprint: 'z', outcome: 'rejected' },
    ], {
      unselected: [
        item('u1', 'skills', 'a'), item('u2', 'skills', 'b'),
        item('u3', 'skills', 'c', { state: 'existing' }), item('u4', 'skills', 'd', { state: 'conflict' }),
      ],
      missing: ['gone'],
    }))).toBe('1 imported · 2 left out · 2 already here · 2 to review · 1 refused · 1 no longer found')
  })
})
