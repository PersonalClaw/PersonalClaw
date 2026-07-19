/** Shared skill vocabulary. */
export const SOURCE_TONE: Record<string, string> = {
  bundled: 'var(--color-primary)',
  local: 'var(--color-info)',
  installed: 'var(--color-ok)',
  marketplace: 'var(--color-warn)',
  'skills.sh': 'var(--color-warn)',
  native: 'var(--color-primary)',
  'agent-local': 'var(--color-secondary, var(--color-info))',
}

/** Human label for a skill source badge (agent-local shows the owning agent). */
export function sourceLabel(source: string, agent?: string): string {
  if (source === 'agent-local') return agent ? `agent: ${agent}` : 'agent-local'
  return source
}

export function fmtInstalls(n?: number): string {
  if (!n) return ''
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M installs`
  if (n >= 1_000) return `${Math.round(n / 1000)}k installs`
  return `${n} installs`
}
