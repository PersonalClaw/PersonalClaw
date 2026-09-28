import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, act, waitFor, within } from '@testing-library/react'
import { RoutingPanel } from './RoutingPanel'

// ── An unreadable routing table is said, and a write over it is refused in the gateway's words ──
//
// `GET /api/models/routing-policy` answered any failure with `{"enabled": false, "use_cases": []}`,
// so the section's own "Couldn't read" branch was unreachable and a table nobody read showed as
// routing turned off with nothing recorded. It now answers `routing_policy_unreadable` with the
// file and why; the section says it could not read the table, gives the gateway's reason and offers
// the read again. A reorder over an unreadable table is refused (409) before anything is written,
// and the section shows that refusal's own sentence, not a bare "Couldn't save that".

const UNREADABLE = "Couldn't read your routing table, /home/you/.personalclaw/routing_policy.json: it is not valid JSON (line 1, column 16). Until it can be read, routing does not use the orders recorded in it."
const REFUSED = "Couldn't read your routing table, /home/you/.personalclaw/routing_policy.json: it is not valid JSON (line 1, column 16). Nothing was changed: saving this order would replace the whole table. Fix or remove the file, then try again."

const ROWS = [
  {
    use_case: 'reasoning', mode: 'off', pin: '',
    candidates: [{ ref: 'local:qwen', local: true }, { ref: 'cloud:opus', local: false }],
    classes: { short_chat: { order: ['local:qwen', 'cloud:opus'], basis: { source: 'manual' } } },
    order_revisions: { short_chat: 'r1' },
  },
]

const routingPolicy = vi.fn()
const setRoutingOrder = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    routingPolicy: () => routingPolicy(),
    setRoutingPolicy: () => Promise.resolve({}),
    setRoutingOrder: (...a: unknown[]) => setRoutingOrder(...a),
    modelsTelemetry: () => Promise.resolve({ rows: [] }),
    routingProposals: () => Promise.resolve({ count: 0, proposals: [] }),
    personalclawConfig: () => Promise.resolve({
      routing: {
        enabled: true, local_timeout_secs: 20, min_samples: 5, hysteresis: 0.05,
        cloud_quality_margin: 0.1, reproposal_cooldown_days: 14,
      },
    }),
    patchConfig: () => Promise.resolve({}),
  },
}))
vi.mock('../../lib/data', () => ({
  useQuery: (_k: string, fn: () => Promise<unknown>) => {
    const [d, setD] = require('react').useState(undefined)
    require('react').useEffect(() => { void fn().then(setD) }, [])
    return { data: d, refresh: () => {} }
  },
}))

function renderPanel() {
  return render(<RoutingPanel query={{ uc: 'reasoning', qc: 'short_chat' }} setQuery={() => {}} />)
}

const policySection = () => screen.getByRole('heading', { name: /Routing policy/ }).closest('section')!

describe('the routing table section', () => {
  beforeEach(() => {
    routingPolicy.mockReset()
    setRoutingOrder.mockReset()
  })

  it('says it could not read the table, in the gateway\'s words, and reads it again', async () => {
    routingPolicy.mockImplementation(() => Promise.reject(new Error(UNREADABLE)))
    renderPanel()
    expect(await screen.findByText(
      "Couldn't read the routing table, so it is not shown here, and nothing about routing changed.",
    )).toBeInTheDocument()
    expect(within(policySection()).getByText(UNREADABLE)).toBeInTheDocument()
    expect(screen.queryByText(/Routing is currently off globally/), 'an unread table is not routing off').toBeNull()

    routingPolicy.mockImplementation(() => Promise.resolve({ enabled: true, use_cases: ROWS }))
    await act(async () => { within(policySection()).getByRole('button', { name: 'Try again' }).click() })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Move local:qwen later' })).toBeInTheDocument())
  })

  it('a reorder the gateway refuses shows the refusal\'s own sentence', async () => {
    routingPolicy.mockImplementation(() => Promise.resolve({ enabled: true, use_cases: ROWS }))
    setRoutingOrder.mockImplementation(() => Promise.reject(Object.assign(new Error(REFUSED), { code: 'routing_policy_unreadable' })))
    renderPanel()
    const later = await waitFor(() => screen.getByRole('button', { name: 'Move local:qwen later' }))
    await act(async () => { later.click() })
    expect(await within(policySection()).findByText(REFUSED)).toBeInTheDocument()
  })
})
