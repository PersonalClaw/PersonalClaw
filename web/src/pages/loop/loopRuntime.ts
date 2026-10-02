import type { DiscoveredAgent } from '../../lib/api'
import type { RuntimeGroup } from '../../lib/agents'
import { providerMeta } from '../agents/agentMeta'

/** What a loop runs on: PersonalClaw's own worker for its kind (`provider` empty), or an agent CLI
 *  (`provider` = its runtime id, `acp:<cli>`) as the agent that CLI offered (`provider_agent`, empty
 *  for a CLI that offers one). The two spine fields a loop stores and `POST /api/loops` takes; the
 *  loop's planner and its workers run there (`loop/manager`, `loop/plan_walkthrough`). */
export interface LoopRuntime { provider: string; provider_agent: string }

export const ON_PERSONALCLAW: LoopRuntime = { provider: '', provider_agent: '' }

export function runtimeOf(loop: { provider?: string; provider_agent?: string }): LoopRuntime {
  return { provider: loop.provider || '', provider_agent: loop.provider ? loop.provider_agent || '' : '' }
}

export function runtimeOfAgent(group: RuntimeGroup, agent: DiscoveredAgent): LoopRuntime {
  return { provider: group.providerId, provider_agent: agent.provider_agent }
}

export function sameRuntime(a: LoopRuntime, b: LoopRuntime): boolean {
  return a.provider === b.provider && (a.provider ? a.provider_agent === b.provider_agent : true)
}

/** The fields a write sends for *rt*: both, always, so moving a loop back onto PersonalClaw clears
 *  the CLI it was on rather than leaving it in place. */
export function runtimeFields(rt: LoopRuntime): { provider: string; provider_agent: string } {
  return { provider: rt.provider, provider_agent: rt.provider ? rt.provider_agent : '' }
}

/** A runtime that isn't ready, in words: the runtime's own sentence about it when it has one (it
 *  says what it needs, and where), else its state. No trailing full stop, so it can be quoted. */
export function notReadyWhy(group: RuntimeGroup): string {
  const state = group.state === 'untested' ? 'not tried yet'
    : group.state === 'needs_login' ? 'needs sign-in'
    : group.state === 'not_found' ? 'not installed'
    : 'unavailable'
  return ((group.detail || '').trim() || state).replace(/\.$/, '')
}

/** How a page names what a loop runs on, and — when it can't run there now — why.
 *
 *  `unavailable` is said only from a read of the runtimes set up here: while that read has not
 *  landed (`groups` undefined) nothing is claimed either way. It is not left blank for a runtime
 *  that is gone or not ready, because the loop would otherwise read as running somewhere it can't. */
export function showRuntime(rt: LoopRuntime, groups: RuntimeGroup[] | undefined): { label: string; title: string; unavailable: string } {
  if (!rt.provider) {
    return { label: 'PersonalClaw', title: 'Runs on PersonalClaw — the loop’s own worker, in this gateway.', unavailable: '' }
  }
  const group = groups?.find((g) => g.providerId === rt.provider)
  const cli = providerMeta(rt.provider, group?.label).label
  const agent = group?.agents.find((a) => a.provider_agent === rt.provider_agent)
  const agentName = agent?.name || rt.provider_agent
  const label = agentName && agentName !== cli ? `${cli} · ${agentName}` : cli
  let unavailable = ''
  if (groups && !group) unavailable = `${cli} isn’t set up here: its agent app is not installed or not enabled.`
  else if (group && !group.ready) unavailable = `${cli} isn’t ready: ${notReadyWhy(group)}.`
  else if (group && !group.failure && rt.provider_agent && !agent) {
    unavailable = `${cli} no longer lists ${rt.provider_agent} among its agents.`
  }
  const title = `Runs on ${label} — the agent CLI runs this loop’s planner and workers.`
  return { label, title: unavailable ? `${title} ${unavailable}` : title, unavailable }
}
