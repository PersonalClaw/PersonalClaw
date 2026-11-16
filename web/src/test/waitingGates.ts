import type { WaitingItem } from '../lib/api'
import fixture from './fixtures/waitingGates.json'

/** Each ask a workflow gate makes, keyed as `fixtures/waitingGates.json` keys it. */
export type GateAsk = 'approval' | 'choice' | 'choice_open' | 'text' | 'form' | 'event'

/** What a test names in a gate: the run it is in, its token, the workflow, the step, and the ask's
 *  own words (merged over the fixture's ask, which keeps its kind). */
export interface GateNames {
  run?: string
  token?: string
  workflow?: string
  node?: string
  ask?: Record<string, unknown>
}

type Entry = { item: WaitingItem; sends: Record<string, unknown> }
const GATES = fixture.gates as unknown as Record<GateAsk, Entry>

/** The fixture's key for an ask a test writes as `{ kind, prompt, choices, fields }`. */
export function gateAskKey(ask: Record<string, unknown>): GateAsk {
  const kind = typeof ask.kind === 'string' && ask.kind ? ask.kind : 'approval'
  if (kind === 'choice') return Array.isArray(ask.choices) && ask.choices.length > 0 ? 'choice' : 'choice_open'
  if (kind === 'approval' || kind === 'text' || kind === 'form' || kind === 'event') return kind
  throw new Error(`no gate in the fixture asks ${kind}`)
}

/** A run's gate as `GET /api/waiting` serves it: the shared fixture, which
 *  `tests/test_a_gates_waiting_item_answers_on_the_route_its_ask_takes.py` holds to what the gateway
 *  lists and answers through its real handlers. So a test can stub a gate only on the route the
 *  gateway names for its ask (an approval on `/confirm`, every other ask on `/resume`). */
export function fixtureGate(key: GateAsk, names: GateNames = {}): WaitingItem {
  const run = names.run ?? `run-${key}`
  const token = names.token ?? `tok-${key}`
  const raw = JSON.stringify(GATES[key].item).split(`run-${key}`).join(run).split(`tok-${key}`).join(token)
  const item = JSON.parse(raw) as WaitingItem
  const brief = { ...(item.brief as Record<string, unknown>) }
  const ask: Record<string, unknown> = { ...(brief.ask as Record<string, unknown>), ...(names.ask ?? {}), kind: (brief.ask as { kind: string }).kind }
  if (names.workflow) brief.workflow = names.workflow
  if (names.node) {
    brief.node_id = names.node
    ask.node_id = names.node
  }
  brief.ask = ask
  return { ...item, brief }
}

/** What Needs you posts on a gate's route, beside its token: the verb or the answer the fixture's
 *  test sends through the gateway's real handler, where it lands. */
export function fixtureSends(key: GateAsk): Record<string, unknown> {
  return { ...GATES[key].sends }
}
