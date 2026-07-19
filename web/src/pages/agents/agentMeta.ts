import { Bot, Cpu } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

/** Provider id → display label + icon + tone for the Agents list. */
export interface ProviderMeta { label: string; icon: LucideIcon; tone: string }
export function providerMeta(providerId: string): ProviderMeta {
  if (providerId === 'native' || !providerId) return { label: 'Native', icon: Bot, tone: 'var(--color-primary)' }
  if (providerId.includes('claude')) return { label: 'Claude Code', icon: Cpu, tone: 'var(--color-info)' }
  if (providerId.includes('codex')) return { label: 'Codex', icon: Cpu, tone: 'var(--color-warn)' }
  // acp:<cli> → Title-case the cli name
  const cli = providerId.replace(/^acp:/, '')
  return { label: cli.replace(/[-_]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()), icon: Cpu, tone: 'var(--color-on-surface-low)' }
}

export const APPROVAL_MODES = [
  { key: '', label: 'Default (hook-based)' },
  { key: 'auto', label: 'Auto-approve all' },
]

/** Reserved built-in agents (the background-chore worker + the goal-loop worker
 *  and goal-planner) are vended by the native provider but the system depends on their fixed
 *  definition, so they're shown READ-ONLY — not editable or deletable. The
 *  server is the source of truth: `/api/agents` marks each agent `reserved`. */
export function isReservedAgent(a: { reserved?: boolean }): boolean {
  return a.reserved === true
}
