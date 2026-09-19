import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react'
import type { NodeInspect, PendingApproval, WorkflowNodeState, WorkflowRunDetailData } from '../lib/api'
import type { WsMessage } from '../lib/useChatSocket'
import { approvalDestination } from './approvalDestination'

// ── #258: the approval nudge must point somewhere that RESOLVES ──────────────────────────────────
//
// A workflow stage's approval carries a SYNTHETIC session key — `workflow:<run>:<node>` — and the
// nudge spent it verbatim: *"open workflow:11b9a34c:synthesize to respond."* That key is not a
// chat (`/api/chat/sessions/<key>` → 404) and has no SPA route, so the only notification a user
// got about a blocked run led nowhere, and the run read as hung.
//
// 🪤 A TOAST THAT RENDERS A LINK IS NOT A TOAST THAT RESOLVES. Asserting `detail.href` is a string,
// or that the message no longer contains the raw key, would pass on a link to the wrong run, to no
// node, or to a param the router drops. So the href the HOOK emits is fed to the REAL `useHashRoute`
// parser and through the REAL `WorkflowsSection`, and the assertion is that the node inspector
// fetched THAT node — the same standard `workflowCardNodeDeepLink.test.tsx` holds the chat card to,
// and the reason this file lives in `app/` rather than under `pages/` (see that file's note: a page
// test may not write `window.location.hash`, and `parseHash` reads it itself).
//
// The vacuity floors are below: a chat session must keep its own destination (the fix must not
// re-route every approval through the workflow view), and landing without the node param must open
// no drawer (a drawer that mounted unconditionally would satisfy the positive leg).

const NODES: WorkflowNodeState[] = [
  { instance_path: 'root.recall', node_id: 'recall', state: 'done' },
  { instance_path: 'root.synthesize', node_id: 'synthesize', state: 'running' },
]

function snapshot(): WorkflowRunDetailData {
  return {
    run_id: '11b9a34c', workflow: 'knowledge-synthesis', status: 'running', spec_version: 2,
    nodes: NODES.map((n) => ({ ...n })),
  } as WorkflowRunDetailData
}

const INSPECT: NodeInspect = {
  run_id: '11b9a34c', node_id: 'synthesize', instance_path: 'root.synthesize', state: 'running',
  resolved_prompt: 'Write ONE consolidated article', resolved_inputs: {},
  output: '', attempts: [], ledger_events: [], cached: false,
}

/** The approval the nudge fires for — the one from #258, verbatim off `GET /api/approvals`. */
const PENDING: PendingApproval = {
  id: 'spawn:b961a327', source: 'subagent',
  tool: 'subagent_run(Write ONE consolidated article)',
  session: 'workflow:11b9a34c:synthesize', ts: 0,
}

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
        // The SAME approval the nudge fired for, still pending. Not an empty queue: with one, the
        // whole card could be unmounted from the run page and every leg here would still pass —
        // measured, and it is the "a component with no call site" trap. The destination has to be
        // proved to CARRY the control, not merely to render.
        approvals: async () => [PENDING],
        resolveApproval: async () => ({ ok: true }),
        workflowRunNodeInspect,
      },
    }
  })
}

/** Drive one `approval` frame through the shell hook and return the toast detail it dispatched. */
async function nudgeFor(session: string): Promise<{ message: string; href: string; hrefLabel: string }> {
  let onMessage: ((m: WsMessage) => void) | null = null
  vi.resetModules()
  vi.doMock('../lib/useChatSocket', () => ({
    useChatSocket: (cb: (m: WsMessage) => void) => { onMessage = cb },
  }))
  const { useApprovalToasts } = await import('./useApprovalToasts')
  const details: Array<{ message: string; href: string; hrefLabel: string }> = []
  const onToast = (e: Event) => {
    const d = (e as CustomEvent).detail || {}
    details.push({ message: String(d.message ?? ''), href: String(d.href ?? ''), hrefLabel: String(d.hrefLabel ?? '') })
  }
  window.addEventListener('ne:toast', onToast)
  try {
    renderHook(() => useApprovalToasts(''))
    expect(onMessage, 'the socket handler must be captured or nothing is driven').not.toBeNull()
    act(() => onMessage!({
      type: 'approval',
      data: { session, id: `ap-${session}`, tool: 'subagent_run(Write ONE consolidated article)', source: 'subagent' },
    }))
  } finally {
    window.removeEventListener('ne:toast', onToast)
    cleanup()
  }
  expect(details, 'the nudge must fire for a session the user is not viewing').toHaveLength(1)
  return details[0]
}

/** Mount the workflows route at `hash` through the REAL hash router (not stubbed on purpose:
 *  re-deriving `sub`/`query` here would let the nudge and the section agree on a param the
 *  shipped parser drops, which is exactly the failure this has to be proof against). */
async function mountRouteAt(hash: string) {
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

describe('the approval nudge for a workflow stage resolves to a surface that can answer it', () => {
  it('stops instructing the user to open a key that 404s', async () => {
    // The shipped sentence was *"open workflow:11b9a34c:synthesize to respond."* — the one
    // instruction the user got, naming a thing with no route. Asserted on its own leg so the
    // sentence defect is visible independently of the link mechanism below.
    const nudge = await nudgeFor('workflow:11b9a34c:synthesize')
    expect(nudge.message).not.toContain('workflow:11b9a34c:synthesize')
    expect(nudge.message).toContain('open the synthesize step of workflow run 11b9a34c to respond.')
  })

  it('links to the run and carries the blocked node', async () => {
    const nudge = await nudgeFor('workflow:11b9a34c:synthesize')
    expect(nudge.href).toBe('#/workflows/runs/11b9a34c?node=synthesize')
  })

  it('the href the nudge emits opens the run ON the blocked node', async () => {
    const nudge = await nudgeFor('workflow:11b9a34c:synthesize')
    await mountRouteAt(nudge.href)
    // The drawer asked the endpoint for the deep-linked node — RESOLUTION, not mere rendering.
    await waitFor(() => expect(workflowRunNodeInspect).toHaveBeenCalledWith('11b9a34c', 'synthesize'))
    // …and the node's own fetched content is on screen, so the user lands reading it. Asserted on
    // the resolved prompt rather than the text "synthesize", which the run's node row also carries
    // — a bare text match would pass with the drawer closed.
    expect(await screen.findByTestId('node-inspector-body')).toBeTruthy()
    expect(await screen.findByTestId('resolved-prompt')).toHaveTextContent('Write ONE consolidated article')
  })

  it('and the surface it lands on can ANSWER that approval', async () => {
    // The point of the whole change. A link that resolves to a page with no control is a nicer
    // dead end, so the destination is required to carry the pair — for THIS approval, named, on
    // the surface the nudge sent the user to.
    const nudge = await nudgeFor('workflow:11b9a34c:synthesize')
    await mountRouteAt(nudge.href)
    expect(await screen.findByRole('button', { name: /Approve: subagent_run/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Reject: subagent_run/ })).toBeTruthy()
  })

  it('🪤 VACUITY: landing without the node param opens no drawer', async () => {
    // Without this, a drawer that mounted on every arrival would pass the leg above while the
    // deep link did nothing at all.
    await mountRouteAt('#/workflows/runs/11b9a34c')
    await waitFor(() => expect(screen.queryByText('knowledge-synthesis')).not.toBeNull())
    expect(screen.queryByTestId('node-inspector-body')).toBeNull()
    expect(workflowRunNodeInspect).not.toHaveBeenCalled()
  })

  it('🪤 VACUITY: a CHAT session keeps its own destination, not the workflow view', async () => {
    // The fix must be a parse of the key's shape, not a blanket re-route: an ordinary
    // chat/subagent approval is answered on the chat page and its sentence already worked.
    const nudge = await nudgeFor('main')
    expect(nudge.href).toBe('#/chat/main')
    expect(nudge.message).toContain('open main to respond.')
  })
})

describe('the sentence and the link cannot name different places', () => {
  it('every destination the grammar produces is a hash route, and the label describes it', () => {
    for (const session of ['main', 'dashboard:abc', 'workflow:r1:root.draft']) {
      const dest = approvalDestination(session)
      expect(dest.href, session).toMatch(/^#\//)
      // The link must say where it goes; a bare "Open" is what the raw key was.
      expect(dest.linkLabel.toLowerCase(), session).toMatch(/workflow run|chat/)
      expect(dest.label, session).not.toBe('')
    }
  })

  it('an instance path with a dot survives the round trip into the run view', () => {
    // `root.draft` is what the engine broadcasts for a nested node; a destination that dropped
    // or mangled it would open the run on the wrong step.
    expect(approvalDestination('workflow:r1:root.draft').href).toBe('#/workflows/runs/r1?node=root.draft')
  })
})
