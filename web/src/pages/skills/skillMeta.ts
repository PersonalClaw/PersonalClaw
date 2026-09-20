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

/** HOW a skill came to exist, which the tier in `source` cannot say (#576).
 *
 *  Deliberately a SECOND marker rather than extra values folded into `sourceLabel`: a
 *  taught skill lives in the same `local` (or `agent-local`) tier as a hand-placed one and
 *  is just as editable, so the two facts answer different questions and the surfaces that
 *  branch on `source` must keep reading the tier. Most skills are hand-authored and return
 *  `null` here — nothing renders, so the common row is unchanged.
 *
 *  `title` earns the marker its place: "taught" alone does not say what taught it, and the
 *  reason to want this label at all is to review what a session put in the library. */
export function provenanceMeta(provenance?: string): { label: string; title: string; tone: string } | null {
  if (provenance === 'taught')
    return { label: 'taught', title: 'Taught in a session, then promoted to the library', tone: 'text-ok' }
  if (provenance === 'auto')
    return { label: 'auto', title: 'Extracted automatically from session activity', tone: 'text-info' }
  return null
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
