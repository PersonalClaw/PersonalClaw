import { useState } from 'react'
import type { ChatSessionSummary, RunningRow, WaitingItem, WaitingList } from '../lib/api'
import type { RouteProps } from '../app/useQueryState'
import { Home } from '../pages/dashboard/Home'

/** Home, mounted with the route props the shell hands it: its address's query is state here, so a
 *  selection (`?open=`) or a view (`?view=`) a test makes is what the next render reads, as a hash
 *  change is in the app. `navigate` is the test's, to assert where a control goes.
 *
 *  Lives in `src/test/` beside the vitest setup file: it is test support, not app code, and nothing
 *  in the app imports it. The `api` a test mocks is its own (`vi.mock` is per file). */
export function HomeHarness({ query: initial = {}, navigate = () => {}, onQuery }: {
  query?: Record<string, string>
  navigate?: RouteProps['navigate']
  onQuery?: (query: Record<string, string>) => void
}) {
  const [query, setQueryState] = useState<Record<string, string>>(initial)
  const setQuery: RouteProps['setQuery'] = (patch) => {
    setQueryState((was) => {
      const next = { ...was }
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === undefined || v === '') delete next[k]
        else next[k] = v
      }
      onQuery?.(next)
      return next
    })
  }
  return <Home sub="" navEpoch={0} navigate={navigate} query={query} setQuery={setQuery} />
}

/** One waiting item as `GET /api/waiting` serves it (`personalclaw.waiting._item`): an approval of
 *  a chat's shell call, unless `over` says otherwise. */
export function waitingItem(over: Partial<WaitingItem> = {}): WaitingItem {
  return {
    id: 'approval:appr-1', kind: 'approval', work: 'chat:chat-1', session: 'chat-1',
    brief: { tool: 'shell.run', tool_input: { command: 'ls' }, tool_purpose: 'List the folder', source_label: 'chat “Release notes”', confirms: [] },
    consequence: 'changes', risk: 'caution', asked_at: '2026-10-21T10:00:00Z', age_secs: 60,
    expires_at: null, current: true, interrupt: 'deliver',
    answer: { method: 'POST', path: '/api/approvals/appr-1/{answer}', takes: ['approve', 'reject'], body: {} },
    route: '#/chat/chat-1',
    ...over,
  }
}

/** A run's gate, as the list serves one: answered at the run's resume route with its token. */
export function gateItem(ask: Record<string, unknown> = { kind: 'choice', prompt: 'Which branch should I push?', choices: ['Ship it', 'Hold'] }, over: Partial<WaitingItem> = {}): WaitingItem {
  return waitingItem({
    id: 'gate:run-77:gate.push:1', kind: 'gate', work: 'workflow:run-77', session: '',
    brief: { workflow: 'release', node_id: 'gate.push', ask, handoff: {} },
    consequence: ask.kind === 'approval' ? 'changes' : 'asks', risk: '',
    answer: { method: 'POST', path: '/api/workflows/runs/run-77/resume', takes: [ask.kind], body: { resume_token: 'tok-9' } },
    route: '#/workflows/runs/run-77',
    ...over,
  })
}

/** The list, with no source unread unless said. */
export function waitingList(waiting: WaitingItem[] = [], unreadable: string[] = []): WaitingList {
  // Any string: a test hands Home a source the gateway names that this build does not know.
  return { waiting, unreadable: unreadable as WaitingList['unreadable'] }
}

/** A running list row (`running_now`): a chat with its turn running. */
export function runningRow(over: Partial<RunningRow> = {}): RunningRow {
  return {
    work: 'chat:chat-1', kind: 'chat', session: 'chat-1', phase: 'tool', line: 'Running shell', since: null,
    name: 'nightly sweep', agent: '', started_at: null, route: '#/chat/chat-1', blocked: false, waiting: [], ...over,
  }
}

/** One of your chats in the chat list, idle. */
export function thread(key: string, title: string, over: Partial<ChatSessionSummary> = {}): ChatSessionSummary {
  return {
    key, title, messages: 4, last_ts: '2026-10-21T09:30:00Z',
    run_state: {
      work: `chat:${key}`, session: key, kind: 'chat', phase: 'idle', line: '', since: null, turn: null,
      last_step_at: null, stopping: false, steerable: false, last_outcome: 'complete', queued_messages: 0, seq: 0,
      waiting: [], helpers_waiting: [], wait: null,
    },
    ...over,
  } as ChatSessionSummary
}
