import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import type { NodeInspect, WorkflowNodeState, WorkflowRunDetailData } from '../lib/api'

// ── WV-10 clause 3: the chat card ROUTES to the one inspector ────────────────────────────────────
//
// The card renders exactly ONE node — the active one — under its own comment *"the currently-
// interesting node, not the whole list — a chat card is a glance"*. So the atom's original
// "any node in WorkflowProgressCard" was contradicted by shipped design, and the re-authored
// clause narrows it: the active-node row becomes the affordance and DEEP-LINKS into the run
// view's existing `NodeInspectorDrawer`, arriving with that node already open.
//
// 🪤 THE FALSE FIX IS A SECOND DRAWER. Mounting `NodeInspectorDrawer` inside the chat card would
// make the link unnecessary and give one job two inspectors that then drift — so the clause fails
// outright if one appears under `pages/chat`, and `the card hosts no inspector of its own` below
// is that grep floor as a rail rather than a promise.
//
// 🪤 A LINK THAT RENDERS IS NOT A LINK THAT RESOLVES. Asserting an `<a>` exists would pass on a
// link to the wrong run, to no node, or to a param the router drops. So the card's emitted href is
// fed to the REAL `useHashRoute` parser and through `WorkflowsSection`, and the assertion is that
// the inspector fetched THAT node id. `landing without ?node= opens no drawer` is its vacuity
// floor: a drawer that mounted unconditionally would satisfy the positive case.
//
// WHY THIS FILE LIVES IN `app/` AND NOT `pages/chat/` — do not move it back. Feeding the href to
// the real parser means seeding the browser's URL, and `parseHash` reads `window.location.hash`
// itself (it takes only a fallback), so there is no pure entry point to call instead.
// `tests/test_url_navigation_doctrine.py` bans every way to write that URL — `location.hash =`,
// `history.pushState/replaceState`, `location.replace(` — under `web/src/pages`, because a *page*
// must route through `navigate`/`setQuery`. Exempting the file was rejected on the precedent
// `pages/dashboard/pinnedTilesSafeMode.test.tsx` already records ("the fix is to stop needing the
// URL, not to exempt the file"), and stubbing the router was rejected above. What is left is that
// this is not a page unit test at all: it mounts a whole section through the real router across two
// page trees, which is exactly what `app/navDisclosure.test.tsx` and `app/routeTransition.test.tsx`
// do — `app/` is the sanctioned home for router-integration tests, and every assertion below keeps
// its original strength here.

const NODES: WorkflowNodeState[] = [
  { instance_path: 'root.plan', node_id: 'plan', state: 'done' },
  { instance_path: 'root.draft', node_id: 'draft', state: 'running' },
  { instance_path: 'root.review', node_id: 'review', state: 'waiting' },
]

function snapshot(over: Partial<WorkflowRunDetailData> = {}): WorkflowRunDetailData {
  return {
    run_id: 'r1', workflow: 'deep-research', status: 'running', spec_version: 2,
    nodes: NODES.map((n) => ({ ...n })), ...over,
  } as WorkflowRunDetailData
}

const INSPECT: NodeInspect = {
  run_id: 'r1', node_id: 'draft', instance_path: 'root.draft', state: 'running',
  resolved_prompt: 'draft the section', resolved_inputs: { outline: 'x' },
  output: '', attempts: [], ledger_events: [], cached: false,
}

// Both surfaces subscribe; neither needs a live EventSource for this.
vi.mock('../pages/workflows/useWorkflowStream', () => ({ useWorkflowStream: () => ({ connected: true }) }))

/** The node-inspect fetch, shared so a test can assert WHICH node the drawer asked for. */
const workflowRunNodeInspect = vi.fn<(runId: string, nodeId: string) => Promise<NodeInspect>>()

async function mockApi() {
  vi.resetModules()
  workflowRunNodeInspect.mockReset()
  workflowRunNodeInspect.mockResolvedValue(INSPECT)
  vi.doMock('../lib/api', async (orig) => {
    const real = await orig<typeof import('../lib/api')>()
    return {
      ...real,
      api: {
        ...real.api,
        workflowRun: async () => snapshot(),
        workflowContinuations: async () => ({ continuations: [] }),
        workflowRunNodeInspect,
      },
    }
  })
}

/** Render the chat card and return the href its active-node row links to. */
async function cardActiveNodeHref(): Promise<string> {
  await mockApi()
  const { WorkflowProgressCard } = await import('../pages/chat/WorkflowProgressCard')
  await act(async () => {
    render(<WorkflowProgressCard refObj={{ runId: 'r1', created: true }} />)
    await new Promise((res) => setTimeout(res, 0))
  })
  const link = await screen.findByRole('link', { name: /Inspect the current step/ })
  return link.getAttribute('href') ?? ''
}

/** Mount the workflows route at `hash` through the REAL hash router.
 *
 *  The router is not stubbed on purpose: re-deriving `sub` + `query` in the test would let the
 *  card and the section agree on a param the shipped parser drops, which is precisely the failure
 *  the deep link has to be proof against. */
async function mountRouteAt(hash: string) {
  // Drop the card if one is mounted: this helper is called after `cardActiveNodeHref` harvested the
  // href, and leaving it up would put two surfaces' copies of every node label in one document.
  cleanup()
  await mockApi()
  window.location.hash = hash
  const { useHashRoute } = await import('./useHashRoute')
  const { WorkflowsSection } = await import('../pages/workflows/WorkflowsSection')
  function Harness() {
    const { sub, navigate, navEpoch, query, setQuery } = useHashRoute('workflows')
    return <WorkflowsSection sub={sub} navigate={navigate} navEpoch={navEpoch} query={query} setQuery={setQuery} />
  }
  await act(async () => {
    render(<Harness />)
    await new Promise((res) => setTimeout(res, 0))
  })
}

beforeEach(() => { window.location.hash = '' })

describe('the chat card deep-links its active node into the run inspector', () => {
  it('links the active node to its run AND carries that node id', async () => {
    const href = await cardActiveNodeHref()
    // `draft` is the RUNNING node; `review` is merely waiting and `plan` is done, so this also
    // pins that the card picked the currently-interesting one rather than the first row.
    expect(href).toBe('#/workflows/runs/r1?node=draft')
  })

  it('the href the card emits opens the inspector ON that node', async () => {
    const href = await cardActiveNodeHref()
    await mountRouteAt(href)

    // The drawer asked the endpoint for the deep-linked node — resolution, not mere rendering.
    await waitFor(() => expect(workflowRunNodeInspect).toHaveBeenCalledWith('r1', 'draft'))
    // …and it is on screen showing THAT node's reconstructability set, so the user lands reading
    // it. Asserted on the fetched content rather than on the text "draft", which the run's own
    // node row also carries — a bare text match would pass with the drawer closed.
    expect(await screen.findByTestId('node-inspector-body')).toBeTruthy()
    expect(await screen.findByTestId('resolved-prompt')).toHaveTextContent('draft the section')
  })

  it('landing without ?node= opens no drawer (the vacuity floor)', async () => {
    // Without this, a drawer that mounted on every arrival would pass the test above while the
    // deep link did nothing at all.
    await mountRouteAt('#/workflows/runs/r1')
    await waitFor(() => expect(screen.queryByText('deep-research')).not.toBeNull())
    expect(screen.queryByTestId('node-inspector-body')).toBeNull()
    expect(workflowRunNodeInspect).not.toHaveBeenCalled()
  })
})

describe('the card stays a glance, not a node list', () => {
  it('renders exactly one node row even with three nodes', async () => {
    await cardActiveNodeHref()
    // The clause fails if the card is grown into a list to satisfy "any node": three nodes in the
    // run, one row in the card. `plan` and `review` must not appear.
    expect(screen.getAllByRole('link', { name: /Inspect the current step/ })).toHaveLength(1)
    expect(screen.queryByText('plan')).toBeNull()
    expect(screen.queryByText('review')).toBeNull()
  })

  it('the card hosts no inspector of its own (the clause grep floor)', () => {
    // `git grep '<NodeInspectorDrawer' -- web/src/pages/chat` must return ZERO: the run view owns
    // the one inspector, and the card routes to it.
    const CHAT = [join(process.cwd(), 'src/pages/chat'), join(process.cwd(), 'web/src/pages/chat')]
      .find(existsSync)
    expect(CHAT, 'the chat page dir must be findable — a moved dir makes this rail vacuous').toBeTruthy()
    const files = readdirSync(CHAT as string).filter((f) => f.endsWith('.tsx') && !f.includes('.test.'))
    // VACUITY FLOOR: the scan has to have found the card it polices.
    expect(files).toContain('WorkflowProgressCard.tsx')
    const offenders = files.filter((f) =>
      readFileSync(join(CHAT as string, f), 'utf8').includes('<NodeInspectorDrawer'))
    expect(
      offenders,
      `${offenders.join(', ')} mounts a second NodeInspectorDrawer. The chat card must ROUTE to the ` +
        `run view's inspector (#/workflows/runs/<id>?node=<node_id>), not host its own — two ` +
        `inspectors for one job drift apart.`,
    ).toEqual([])
  })
})
