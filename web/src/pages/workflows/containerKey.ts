/** Stream-key equivalence between loop cockpits and template runs (LOOPS-EVOLUTION R10c).
 *
 *  The loop cockpit keys its per-run SSE on `loop:<id>`. A template run streams under a
 *  run-scoped key. Comparing those with `===` is a proven regression class here, and a
 *  nasty one: the stream connects, the cockpit renders, no error appears anywhere, and
 *  nothing ever updates. There is no failure to see — only absence.
 *
 *  So "is this event mine?" is answered by comparing BASE CONTAINERS, not raw keys. The
 *  backend has the same function (`workflows/loop_aliases.py`) and a test asserts the two
 *  agree; a divergence here would reintroduce exactly the silent drop this closes. */

/** Prefixes a container key may carry, longest-first.
 *
 *  Order matters: `workflow:run:abc` must match the longer prefix before `workflow:`, or a
 *  shortest-first scan leaves `run:abc` behind and the comparison fails on a key that
 *  should have matched. */
const KEY_PREFIXES = ['workflow:run:', 'workflow:', 'loop:', 'run:'] as const

/** Strip any stream-key prefix down to the bare container id. */
export function baseContainer(key: string | null | undefined): string {
  const raw = (key ?? '').trim()
  for (const prefix of KEY_PREFIXES) {
    if (raw.startsWith(prefix)) return raw.slice(prefix.length)
  }
  return raw
}

/** Do these two stream keys name the same container?
 *
 *  Empty keys are never equivalent. Treating two blanks as a match would route every
 *  unkeyed event to every open cockpit, which is a louder bug than the one this fixes but
 *  a bug all the same. */
export function keysEquivalent(
  left: string | null | undefined,
  right: string | null | undefined,
): boolean {
  const a = baseContainer(left)
  const b = baseContainer(right)
  if (!a || !b) return false
  return a === b
}

/** Legacy loop kind → the template that replaces it.
 *
 *  Mirrors `KIND_TO_TEMPLATE` in `workflows/loop_aliases.py`, and a test asserts the two
 *  tables are identical — the picker offering a template the backend cannot resolve is a
 *  dead menu entry, and the drift would only show when a user clicked it. */
export const KIND_TO_TEMPLATE: Readonly<Record<string, string>> = {
  general: 'general-project',
  goal: 'goal-pursuit-open-ended',
  code: 'code-implementation',
  design: 'design-project',
  research: 'deep-research',
}

/** The template for a legacy loop kind, or '' when there is no alias.
 *
 *  No default on an unknown kind: starting the wrong workflow is harder to debug than
 *  starting none, because "it ran something" hides the mistake. */
export function templateForKind(
  kind: string | null | undefined,
  opts: { variant?: string; hasVerifyCommand?: boolean } = {},
): string {
  const normalized = (kind ?? '').trim().toLowerCase()
  if (!normalized) return ''
  if (normalized === 'goal') {
    const variant = (opts.variant ?? '').trim().toLowerCase()
    if (variant === 'verifiable') return 'goal-pursuit-verifiable'
    if (variant === 'open-ended' || variant === 'open_ended') return 'goal-pursuit-open-ended'
    // A goal loop carrying a command that proves it WAS the verifiable variant in all but
    // name; honouring that beats dropping the user into a template that ignores the
    // command they already supplied.
    if (opts.hasVerifyCommand) return 'goal-pursuit-verifiable'
  }
  return KIND_TO_TEMPLATE[normalized] ?? ''
}
