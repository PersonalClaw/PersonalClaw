import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { DiagnosticsPanel } from './DiagnosticsPanel'

// ── Two identical-looking pickers, one of which writes a persistent setting ───────────────────────
//
// `#/settings/diagnostics` renders two one-of-N pill groups whose visible text was byte-identical:
//
//   Backend log level   DEBUG INFO WARNING ERROR   Current: WARNING     ← WRITES a backend setting,
//                                                                          persisted across restarts
//   Live logs toolbar   DEBUG INFO WARNING ERROR                        ← filters what is on SCREEN
//
// Their accessible names already distinguished them — `Backend log level: DEBUG` versus
// `Show DEBUG and above`, added by an earlier cycle whose own comment says "the two dimensions are
// genuinely different … which is exactly why each needs to say which one it is". It said it in the
// accessibility tree only. **So the one reader who could tell a persistent backend write from a local
// view filter was the one using a screen reader**, and a sighted user had to infer it from position.
// This file pins both halves: the names stay distinct, and the visible surface says it too.
//
// 🪤 THE CLAIM I COULD NOT SUBSTANTIATE, recorded so the next cycle does not re-derive it. The view
// filter can be set to DEBUG while the backend logger is at WARNING, and reading the code that looks
// like a dead end (a logging handler only receives what the logger's level admits, and the SSE stream
// attaches its handler to the `personalclaw` logger). Driving it did NOT confirm a user-visible
// consequence: at backend=WARNING the panel still showed an INFO line, because `/api/logs` replays a
// ring buffer captured before the level changed — and an idle gateway emits no DEBUG records at either
// level, so 2 lines became 3. A note reading "the backend is not emitting these" would therefore be
// asserting a mechanism the surface does not demonstrate. Left unfixed on purpose.

const logLevel = vi.fn()
const setLogLevel = vi.fn()
const logsUrl = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    logLevel: (...a: unknown[]) => logLevel(...a),
    setLogLevel: (...a: unknown[]) => setLogLevel(...a),
    logsUrl: (...a: unknown[]) => logsUrl(...a),
  },
}))

describe('the two level pickers are distinguishable by sight, not only by name', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    logLevel.mockResolvedValue('WARNING')
    logsUrl.mockReturnValue('http://127.0.0.1:0/api/logs?lines=300')
    // jsdom has no EventSource; the panel constructs one inside try/catch and returns early, so it
    // mounts with an empty log — which is the state this test needs anyway.
  })

  it('each group still announces its own dimension', async () => {
    render(<DiagnosticsPanel />)
    await waitFor(() => expect(screen.getByLabelText('Backend log level: WARNING')).toBeTruthy())
    // Same four words, two dimensions. Both spellings must exist, or the groups have merged.
    for (const level of ['DEBUG', 'INFO', 'WARNING', 'ERROR']) {
      expect(screen.getByLabelText(`Backend log level: ${level}`), level).toBeTruthy()
      expect(screen.getByLabelText(`Show ${level} and above`), level).toBeTruthy()
    }
  })

  it('exactly one option is pressed in each group, and they are set independently', async () => {
    render(<DiagnosticsPanel />)
    await waitFor(() => expect(screen.getByLabelText('Backend log level: WARNING').getAttribute('aria-pressed')).toBe('true'))
    // The backend reports WARNING; the view floor defaults to DEBUG. Two different values at once is
    // the whole reason the visible label matters.
    expect(screen.getByLabelText('Show DEBUG and above').getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByLabelText('Show WARNING and above').getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByLabelText('Backend log level: DEBUG').getAttribute('aria-pressed')).toBe('false')
  })

  it('the view filter carries a VISIBLE label, adjacent to its own pills', async () => {
    render(<DiagnosticsPanel />)
    await waitFor(() => expect(screen.getByLabelText('Show DEBUG and above')).toBeTruthy())
    const label = screen.getByText('Show', { selector: 'span' })
    // Adjacency, not mere presence: a label elsewhere on the page names nothing.
    const pills = screen.getByLabelText('Show DEBUG and above').parentElement!
    expect(label.nextElementSibling, 'the label sits immediately before the pill group').toBe(pills)
    // Responsive by decision, not by accident: hidden on phone, where the toolbar is tight and the
    // two groups sit far apart under their own headings. Pinned so it is not silently made
    // unconditional (which would risk overflow) or dropped.
    expect(label.className).toContain('hidden')
    expect(label.className).toContain('sm:inline')
  })

  it('the backend group keeps its own visible echo, so the two rows read differently', async () => {
    render(<DiagnosticsPanel />)
    // "Current: WARNING" is the backend row's distinguishing text and predates this change; if it
    // disappears, the rows become identical again from the other side.
    await waitFor(() => expect(screen.getByText('Current:')).toBeTruthy())
    expect(screen.getByText('WARNING', { selector: 'strong' })).toBeTruthy()
  })
})
