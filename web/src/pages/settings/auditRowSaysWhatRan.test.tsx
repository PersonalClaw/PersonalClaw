import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import type { AuditPage } from '../../lib/api'
import { AuditPanel } from './AuditPanel'

// ── The audit log could not answer "what did it run" ────────────────────────────────────────────
//
// Every shell call's row read `bash` and nothing else: the command was in the session's transcript
// only. The server now records it in the row's `resources` (masked, one line, cut at a stated
// bound), and a command refused before it ran keeps it in `metadata.command`. The panel shows both:
// the command beside the tool on the row itself, so the log can be read without opening each row,
// and in full when the row is opened. A row about no call keeps its resources to the opened row, so
// an API row does not echo its own path beside it.

const auditEvents = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    auditEvents: (...a: unknown[]) => auditEvents(...a),
    auditVerify: vi.fn(),
    selRotate: vi.fn(),
  },
}))
vi.mock('../../lib/data', () => ({ invalidateKeys: vi.fn() }))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

const COMMAND = 'pcfixture-deployctl --[REDACTED: credential] status'
const page = (): AuditPage => ({
  events: [
    { event_id: 'e1', timestamp: '2026-10-02T04:21:16Z', event_type: 'tool_invocation', outcome: 'approved', outcome_tone: 'success', operation: 'bash', resources: COMMAND },
    { event_id: 'e2', timestamp: '2026-10-02T04:22:16Z', event_type: 'command_refused', outcome: 'refused', outcome_tone: 'danger', operation: 'step', resources: 'Blocked: the command matches a pattern. It was not run.', metadata: { command: 'pcfixture-cloudctl status', control: 'shell_denylist' } },
    { event_id: 'e3', timestamp: '2026-10-02T04:23:16Z', event_type: 'api_access', outcome: 'ok', outcome_tone: 'success', operation: 'POST /api/chat/screen-frame', resources: '/api/chat/screen-frame' },
  ],
  count: 3, next_cursor: '', scanned: 3, truncated: false,
  outcome_families: [],
})

describe('an audit row says what it ran', () => {
  beforeEach(() => { vi.clearAllMocks(); auditEvents.mockResolvedValue(page()) })

  it('shows a shell call’s command beside its tool, on the row itself', async () => {
    render(<AuditPanel />)
    const row = (await screen.findByText(COMMAND, { exact: false })).closest('button')
    expect(row?.textContent).toContain('bash')
    expect(row?.textContent).toContain(`· ${COMMAND}`)
  })

  it('shows the command in full when the row is opened', async () => {
    render(<AuditPanel />)
    fireEvent.click((await screen.findByText(COMMAND, { exact: false })).closest('button')!)
    expect(screen.getByText('resources:').parentElement?.textContent).toBe(`resources: ${COMMAND}`)
  })

  it('shows the command a refused row kept, on the row and when it is opened', async () => {
    render(<AuditPanel />)
    const row = (await screen.findByText('step', { exact: false })).closest('button')!
    expect(row.textContent).toContain('step · pcfixture-cloudctl status')
    fireEvent.click(row)
    expect(screen.getByText('command:').parentElement?.textContent).toBe('command: pcfixture-cloudctl status')
  })

  it('leaves a row about no call as it was', async () => {
    render(<AuditPanel />)
    const row = (await screen.findByText('POST /api/chat/screen-frame', { exact: false })).closest('button')
    expect(row?.textContent).not.toContain('·')
    fireEvent.click(row!)
    expect(screen.getByText('resources:').parentElement?.textContent).toBe('resources: /api/chat/screen-frame')
  })
})
