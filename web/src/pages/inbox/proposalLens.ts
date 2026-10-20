import type { InboxItem, InboxProposal } from '../../lib/api'

/** The C6 payload on an item, or null. A row without one is not a proposal the lens can
 *  act on — it renders read-only rather than offering an Approve that cannot dispatch. */
export function proposalOf(it: Pick<InboxItem, 'refs'>): InboxProposal | null {
  const raw = it.refs?.proposal
  if (!raw || typeof raw !== 'object') return null
  const p = raw as Partial<InboxProposal>
  if (typeof p.title !== 'string' || typeof p.apply !== 'object' || p.apply === null) return null
  return {
    title: p.title,
    preview: typeof p.preview === 'string' ? p.preview : '',
    preview_kind: p.preview_kind === 'diff' ? 'diff' : 'text',
    provenance: typeof p.provenance === 'string' ? p.provenance : '',
    expires_at: p.expires_at ?? null,
    editable: p.editable === true,
    apply: p.apply as Record<string, Record<string, unknown>>,
  }
}

/** The single apply case name, or '' when the payload does not declare exactly one.
 *  Mirrors the backend's closed set — an unknown key is NOT guessed at here either. */
export const APPLY_CASES = ['action', 'workflow', 'skill_promotion', 'app_callback'] as const
export type ApplyCase = (typeof APPLY_CASES)[number]

export function applyCase(p: InboxProposal): ApplyCase | '' {
  const keys = Object.keys(p.apply ?? {})
  if (keys.length !== 1) return ''
  const k = keys[0] as ApplyCase
  return APPLY_CASES.includes(k) ? k : ''
}

export const APPLY_CASE_LABEL: Record<ApplyCase, string> = {
  action: 'Runs an action',
  workflow: 'Starts a workflow',
  skill_promotion: 'Installs a skill',
  app_callback: 'Calls back into the app',
}

/** The batch-approve grouping key: `(provenance, item kind)`.
 *
 *  🔴 This is the whole reason mixed sweeps are IMPOSSIBLE rather than discouraged. The
 *  batch control is enabled only when every selected row shares this key, so "approve all"
 *  can never mean "approve these four unrelated things" — the failure mode that makes bulk
 *  approval dangerous. Computed in one place so the button's enabled-ness and its label
 *  can't disagree. */
export function groupKey(it: Pick<InboxItem, 'refs' | 'item_kind'>): string {
  const p = proposalOf(it)
  return `${p?.provenance ?? ''}|${it.item_kind ?? ''}`
}

/** Human-readable half of the group, for the batch button's label. */
export function groupLabel(it: Pick<InboxItem, 'refs'>): string {
  const prov = proposalOf(it)?.provenance ?? ''
  if (!prov) return 'unknown source'
  return prov.startsWith('app:') ? `the ${prov.slice(4)} app` : prov
}

/** True only when the selection is non-empty AND single-group. Never true for a mixed
 *  selection — the UI wires the batch button's `disabled` to the negation of this. */
export function canBatchApprove(selected: Array<Pick<InboxItem, 'refs' | 'item_kind'>>): boolean {
  if (selected.length === 0) return false
  const first = groupKey(selected[0])
  return selected.every((it) => groupKey(it) === first)
}

/** Distinct group count — what the disabled control explains to the user. */
export function groupCount(selected: Array<Pick<InboxItem, 'refs' | 'item_kind'>>): number {
  return new Set(selected.map(groupKey)).size
}
