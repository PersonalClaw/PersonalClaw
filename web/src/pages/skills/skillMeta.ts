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

/** What an accepted skill proposal DID — one sentence, shared by both surfaces that accept one.
 *
 *  The Skills card and the inbox proposal panel answer the same proposal through the same
 *  endpoint, so a reviewer who accepts from either has to read the same confirmation; two
 *  wordings for one decision is the drift that makes the two surfaces disagree about what
 *  just happened.
 *
 *  The VERSION is part of the sentence, not a detail: a refinement of a skill that already had
 *  refinements reads identically to its first without it, and "which version did I approve?" is
 *  the only question a refinement raises that the skill name cannot answer. `version` is 0 for
 *  a `kind: 'new'` accept, which creates a skill rather than versioning one. */
export function acceptedLabel(name: string, version?: number): string {
  return version ? `Accepted → ${name} · refinement v${version}` : `Accepted → ${name}`
}

export function fmtInstalls(n?: number): string {
  if (!n) return ''
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M installs`
  if (n >= 1_000) return `${Math.round(n / 1000)}k installs`
  return `${n} installs`
}
