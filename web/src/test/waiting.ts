import type { PendingApproval, WaitingItem, WaitingList } from '../lib/api'

// What waits on you as the gateway serves it (`GET /api/waiting`), for a test to hand a page.

/** An item of *kind*, asked a minute ago, that waits until it is answered. */
export function waitingItem(over: Partial<WaitingItem> & Pick<WaitingItem, 'kind'>): WaitingItem {
  const asked = new Date(Date.now() - 60_000).toISOString()
  return {
    id: `${over.kind}:1`,
    work: '',
    session: '',
    brief: {},
    consequence: 'asks',
    risk: '',
    asked_at: asked,
    age_secs: 60,
    expires_at: null,
    current: true,
    interrupt: 'deliver',
    answer: { method: 'POST', path: '', takes: [], body: {} },
    route: '',
    ...over,
  }
}

/** The approval *ap* as the list carries it: its registry row as its brief. */
export function waitingApproval(ap: PendingApproval, over: Partial<WaitingItem> = {}): WaitingItem {
  return waitingItem({
    kind: 'approval',
    id: `approval:${ap.id}`,
    work: ap.work ?? '',
    session: ap.session,
    brief: { ...ap },
    consequence: ap.risk === 'destructive' ? 'destructive' : 'changes',
    risk: ap.risk,
    asked_at: ap.ts ? new Date(ap.ts * 1000).toISOString() : null,
    answer: {
      method: 'POST',
      path: `/api/approvals/${encodeURIComponent(ap.id)}/{answer}`,
      takes: ['approve', 'reject'],
      body: {},
    },
    ...over,
  })
}

/** The list holding *items*, every source read. */
export function waitingList(...items: WaitingItem[]): WaitingList {
  return { waiting: items, unreadable: [] }
}
