import { type CodeStage } from '../../../lib/api'
import { ACTIVE_LOOP_STATUSES } from '../../../lib/loopStatus'
import { phaseKey } from '../../loops/loopPhases'

// ── What the cockpit's regions share ───────────────────────────────────────────────────────────
//
// The statuses a steer still reaches and the ones that end a run, the key a stage is tracked
// under, a worker path resolved against the workspace, the steer box's auto-grow, and the shape of
// one line of the worker's activity.

// Statuses where a steer can still be acted on: running (applies next cycle) or a
// resumable state (the nudge is queued / resumes the worker). Terminal states
// (complete/stopped) have no next cycle, so the steer box is hidden for them.
// `stagnant` is the state that most NEEDS a steer (the watchdog flags "stalled —
// needs direction") — a code loop can reach it (the shared stagnation check), so it
// must be steerable, not a dead end.
export const STEERABLE = new Set([...ACTIVE_LOOP_STATUSES, 'failed'])

// Terminal, non-resumable statuses — mirrors project.py TERMINAL_STATUSES.
export const TERMINAL_STATUSES = new Set(['complete', 'stopped'])

// Resolve a worker-recorded `files_touched` path against the workspace root — the ONE
// place this logic lives (was copy-pasted into the follow-worker open, the OutcomeBanner
// tally, and the FilesTouched chips, which had already drifted: only one deduped). A
// path comes in three forms: absolute under <root>; a parallel task-worker's worktree
// path (<root>/.pclaw-worktrees/<task_id>/<rel>, deleted post-merge → remap to the
// merged base <root>/<rel>); or a bare relative path (no-workspace/sequential mode
// records "greet.py"). Returns {abs, rel} when the path resolves UNDER the root, or
// null (absolute-but-outside-root, or an unresolvable worktree path); this pure string
// helper does not validate filesystem existence. `root` must already be
// trailing-slash-stripped.
export function resolveTouchedPath(raw: string, root: string): { abs: string; rel: string } | null {
  if (typeof raw !== 'string' || !raw || !root) return null
  let p = raw
  // Workers append human context as "<path> (annotation)" or "<path> — annotation".
  // Strip it before EVERY path classification so absolute, worktree, and relative
  // forms all reach the three consumers as the same pure path.
  for (const separator of [' — ', ' (']) {
    const annotationAt = p.indexOf(separator)
    if (annotationAt >= 0) p = p.slice(0, annotationAt)
  }
  p = p.trim()
  if (!p) return null
  const mk = '/.pclaw-worktrees/'
  const i = p.indexOf(mk)
  if (i >= 0) {
    const rel = p.slice(i + mk.length).split('/').slice(1).join('/')  // drop the <task_id> segment
    p = rel ? `${root}/${rel}` : p
  }
  if (p.includes(mk)) return null  // unresolvable worktree path → dead file
  if (p.startsWith(root + '/')) return { abs: p, rel: p.slice(root.length + 1) }
  if (p.startsWith('/')) {
    // An ABSOLUTE path that doesn't prefix-match `root` is usually a REALPATH form of a
    // file genuinely under a symlinked workspace (worker tools resolve symlinks: /tmp→
    // /private/tmp, /var→/private/var on macOS). A plain prefix check wrongly rejected it
    // as "outside the workspace" → the file's FilesTouched chip vanished, follow-worker
    // skipped it, and the OutcomeBanner tally under-counted. Recover via the workspace's
    // last segment (same marker approach as ChangesPanel/canonPath); re-root under `root`
    // so downstream open/canon paths stay consistent. Still null if truly outside.
    const wsBase = root.split('/').pop() || ''
    const marker = `/${wsBase}/`
    const mi = wsBase ? p.lastIndexOf(marker) : -1
    if (mi >= 0) { const rel = p.slice(mi + marker.length); return { abs: `${root}/${rel}`, rel } }
    return null  // absolute but genuinely OUTSIDE the workspace
  }
  return { abs: `${root}/${p.replace(/^\.?\//, '')}`, rel: p }  // bare relative → under root
}

// The effective key a stage is tracked under in stage_status / task_list_ids — the SHARED
// resolution (`loopPhases.phaseKey`), not a local copy. The backend keys a STAGELESS phase
// (blank stage id — e.g. an unlabeled decomposition phase) by its TITLE everywhere
// (_stage_of/set_stage_status/ensure_stage_lists); indexing by the bare s.stage would miss
// such a phase's status + TaskList (it'd read 'pending'/'no tasks' forever). This local copy
// also never TRIMMED, so a whitespace-only stage id keyed on whitespace instead of falling
// through to the title — the shared reader trims, like the backend does.
export const stageKey = (s: CodeStage): string => phaseKey(s)

// Grow a steer textarea to fit its content (up to the CSS max-height, which then
// scrolls) so a multi-line steer is visible while composing — a rows={1} box hid
// everything past the first line. Reset to auto first so it can also SHRINK back.
export function autoGrowTextarea(el: HTMLTextAreaElement | null) {
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

// One live worker-activity line: a tool call, a status/thinking event, or a
// 'say' line — the worker's own assistant narration (streamed chat_chunk text),
// which makes the panel read like an AI chat sidebar, not just a tool log.
export interface ActivityItem { kind: 'tool' | 'status' | 'say'; label: string; detail?: string; rawLabel?: string }
