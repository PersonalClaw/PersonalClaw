import type { WorkflowScope } from '../../lib/api'

/** Workflow scope vocabulary. scope_ref meaning varies by scope. */
export const SCOPES: { key: WorkflowScope; label: string; tone: string; refLabel?: string; refHint?: string }[] = [
  { key: 'global', label: 'Global', tone: 'var(--color-primary)' },
  { key: 'workspace', label: 'Workspace', tone: 'var(--color-info)', refLabel: 'Working directory', refHint: 'Absolute path this SOP applies in' },
  { key: 'agent', label: 'Agent', tone: 'var(--color-ok)' },
  { key: 'session', label: 'Session', tone: 'var(--color-warn)', refLabel: 'Session key', refHint: 'Session this SOP is scoped to' },
]
const SCOPE_MAP = Object.fromEntries(SCOPES.map((s) => [s.key, s]))
export const scopeMeta = (k?: string) => SCOPE_MAP[k ?? 'global'] ?? SCOPES[0]
export const scopeNeedsRef = (k?: string) => k === 'workspace' || k === 'session'

export function relTime(iso?: string): string {
  if (!iso) return ''
  const t = Date.parse(iso); if (Number.isNaN(t)) return ''
  const s = (Date.now() - t) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return new Date(t).toLocaleDateString()
}
