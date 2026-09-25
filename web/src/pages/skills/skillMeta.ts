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

/** WHY a skill has no install baseline, as far as anything recorded it — the parenthetical on the
 *  inspector's "Unverified" line.
 *
 *  🔴 This was the constant "(bundled or hand-placed)", which is a guess that is wrong for every
 *  skill whose origin IS recorded: a skill created with New skill read as bundled or hand-placed
 *  while its own frontmatter said `source: dashboard`. Only a skill with no marker at all keeps
 *  the either-or, because that is all anything knows about it. `dashboard` gets no row marker in
 *  `provenanceMeta` above — like a hand-placed skill it is the user's own, which is most rows. */
export function noBaselineReason(skill: { source: string; provenance?: string }): string {
  if (skill.source === 'bundled') return 'bundled with PersonalClaw'
  if (skill.provenance === 'dashboard') return 'created in the dashboard'
  if (skill.provenance === 'taught') return 'taught in a session'
  if (skill.provenance === 'auto') return 'extracted from session activity'
  return 'bundled or hand-placed'
}

/** `content` with its frontmatter `name:` set to `name` — in the New-skill dialog, the Name field
 *  is the one source of the skill's identity and the SKILL.md follows it.
 *
 *  🔴 The dialog's template carried a literal `name: my-skill` that nothing updated, while the
 *  server binds that line to the key the Name field sends (`skills/loader.py:validate_skill_md`:
 *  "frontmatter name must match skill key"). So filling in the Name and pressing Create failed on
 *  the first try, every time. The KEY is the source of truth, not the line: it is the directory on
 *  disk and the id every skills route, the loader and agent bindings address the skill by — and the
 *  server refuses a body that disagrees rather than rewriting text the user typed. So the dialog
 *  derives the line, and shows exactly the bytes Create will send.
 *
 *  Finds the block the way the server's parser does (`SkillsLoader._parse_frontmatter_text`):
 *  leading whitespace ignored, `---` first, closed at the first `\n---`. Every top-level `name:`
 *  line is rewritten (that parser keeps the LAST one, so leaving any stale one would still
 *  mismatch); none → one is added as the block's first line. No closed block → unchanged: there
 *  is no frontmatter to follow, and the server names that problem itself. */
export function withFrontmatterName(content: string, name: string): string {
  const block = /^(\s*---\r?\n)([\s\S]*?)(\r?\n---)/.exec(content)
  if (!block) return content
  const [whole, open, body, close] = block
  const line = name.trim() ? `name: ${name.trim()}` : 'name:'
  const next = /^name[ \t]*:/m.test(body)
    ? body.replace(/^name[ \t]*:.*$/gm, line)
    : (body ? `${line}\n${body}` : line)
  return open + next + close + content.slice(whole.length)
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
