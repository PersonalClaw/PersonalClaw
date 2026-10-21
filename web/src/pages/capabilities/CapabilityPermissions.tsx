import { useId, type ReactNode } from 'react'
import { Eyebrow } from '../../ui/Eyebrow'
import type { McpServer, SavedAgent, SkillItem, ToolItem } from '../../lib/api'
import { APPROVAL_MODES } from '../agents/agentMeta'

/** One thing the selected item may do: what it is about, and what holds for this item. */
export interface PermissionRow {
  label: string
  value: ReactNode
}

/** The selected item's permissions in Capabilities' right pane (#3817): what it may do, read from
 *  what the gateway already says about it. It only shows them; each is granted and changed where it
 *  always was (an agent's editor, a server's card, an app's install review), and enforced by the
 *  gateway. An app's and a Store listing's permissions are the disclosure their install review
 *  shows, so those two panes carry that and not this. */
export function CapabilityPermissions({ rows }: { rows: PermissionRow[] }) {
  const id = useId()
  if (rows.length === 0) return null
  return (
    <section aria-labelledby={id} className="flex flex-col gap-s">
      <Eyebrow as="h3" id={id}>Permissions</Eyebrow>
      <dl className="flex flex-col gap-s rounded-md bg-surface-container px-m py-m">
        {rows.map((r) => (
          <div key={r.label} className="flex flex-wrap items-baseline gap-x-m gap-y-xs">
            <dt data-type="label-s" className="w-28 shrink-0 text-on-surface-low">{r.label}</dt>
            <dd data-type="body-s" className="min-w-0 flex-1 text-on-surface">{r.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

/** A list the item names, or what holds when it names none. */
const listed = (items: readonly string[] | undefined, none: string) => (items && items.length > 0 ? items.join(', ') : none)

/** A native agent's permissions: how its tool calls are approved, and the tools and skills it may
 *  use. `runsOn` names the agent CLI it runs on, where PersonalClaw's lists do not hold. The words
 *  are the editor's own (`AgentForm`). */
export function agentPermissions(agent: SavedAgent, runsOn?: string): PermissionRow[] {
  const mode = agent.approval_mode ?? ''
  return [
    { label: 'Approval', value: APPROVAL_MODES.find((m) => m.key === mode)?.label ?? mode },
    {
      label: 'Tools',
      value: runsOn
        ? `Not applied: ${runsOn} runs its own tools, and a tool list holds only on PersonalClaw's own agent.`
        : listed(agent.tools, 'All available tools'),
    },
    {
      label: 'Skills',
      value: runsOn
        ? `Not applied: ${runsOn} loads its own skills, and a skill list holds only on PersonalClaw's own agent.`
        : listed(agent.skills, 'All available skills'),
    },
  ]
}

/** An agent an agent CLI defines and runs: its tools and skills are the CLI's own. */
export function discoveredAgentPermissions(runtime: string): PermissionRow[] {
  return [{
    label: 'Tools and skills',
    value: `${runtime} runs its own tools and loads its own skills, and a tool or skill list holds only on PersonalClaw's own agent.`,
  }]
}

/** A skill's reach: when it loads, and the agents it reaches. An agent with no skill list of its own
 *  may use every skill (`agents/skill_list.py`), so those reach it too; `loaded_by_agents` names the
 *  agents whose list names it. An agent's own copy reaches that agent alone. */
export function skillReach(skill: SkillItem): PermissionRow[] {
  const agents = skill.source === 'agent-local'
    ? `Only ${skill.agent || 'its own agent'}`
    : skill.loaded_by_agents.length > 0
      ? `${skill.loaded_by_agents.join(', ')}, and every agent with no skill list`
      : 'Every agent with no skill list'
  return [
    { label: 'Loads', value: skill.always ? 'Into every session' : 'When it is relevant' },
    { label: 'Agents', value: agents },
  ]
}

const RISK: Record<string, string> = { safe: 'Safe', caution: 'Caution', destructive: 'Destructive' }

/** A tool's permissions: its declared risk and whether it needs approval, and for a tool server's
 *  tool, the trust its server holds: its read-only labels, and whether it may ask you questions
 *  (`questions`, `null` while that could not be read). The words are the server card's own. */
export function toolPermissions(tool: ToolItem, server?: McpServer, questions?: boolean | null): PermissionRow[] {
  const rows: PermissionRow[] = [
    { label: 'Risk', value: tool.risk_level ? RISK[tool.risk_level] ?? tool.risk_level : 'Not declared' },
    { label: 'Approval', value: tool.requires_approval ? 'Needed before it runs' : 'Not needed' },
  ]
  if (!server) return rows
  rows.push({ label: 'Server', value: server.allowed === false ? `${server.name}, waiting for your Allow` : server.name })
  // PersonalClaw's own server carries no trust: its tools say what they do themselves.
  if (server.readOnlyTrust) {
    rows.push({ label: 'Read-only labels', value: server.readOnlyTrust.trusted ? 'Trusted' : 'Not trusted: each of its tools asks' })
  }
  rows.push({
    label: 'Questions',
    value: questions === null || questions === undefined ? 'Couldn’t read whether it may ask you questions'
      : questions ? 'May ask you questions' : 'Can’t ask you questions',
  })
  return rows
}
