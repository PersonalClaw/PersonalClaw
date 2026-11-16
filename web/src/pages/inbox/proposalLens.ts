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
  // Every learning proposal carries this one case, whatever its Accept does: a template saves a
  // template, a lesson records that you agree. Its card says which (`acceptOf`); this is only
  // what a row with no card says.
  skill_promotion: 'Accepts the proposal',
  app_callback: 'Calls back into the app',
}

/** What a learning proposal's Accept does, as its card says it (`learning.review.accept_for`,
 *  filed on the row as `refs.card.accept`, and what Needs you's card reads): `installs`,
 *  `applies`, `records`, or `none` when nothing here can accept it, with the sentence that says
 *  so. Null for a row with no card. The gateway's answer to the press is still the authority. */
export type AcceptSaid = { effect: 'installs' | 'applies' | 'records' | 'none'; sentence: string }
const EFFECTS: ReadonlyArray<AcceptSaid['effect']> = ['installs', 'applies', 'records', 'none']

export function acceptOf(it: Pick<InboxItem, 'refs'>): AcceptSaid | null {
  const card = it.refs?.card
  const accept = card && typeof card === 'object' ? (card as { accept?: unknown }).accept : null
  if (!accept || typeof accept !== 'object') return null
  const { effect, sentence } = accept as { effect?: unknown; sentence?: unknown }
  if (!EFFECTS.includes(effect as AcceptSaid['effect']) || typeof sentence !== 'string') return null
  const said = sentence.trim()
  return said ? { effect: effect as AcceptSaid['effect'], sentence: said } : null
}

/** A refused apply, said once. A learning proposal's refusal (*learning*: its `skill_promotion`
 *  case) is learning's own sentence, which already says the proposal still waits: it is shown as
 *  it is. Any other case's error is the app's or the provider's words, so it keeps the frame
 *  whatever it says: "Not applied: <what it said>. It still waits." */
export function refusalText(error?: string, learning = false): string {
  const said = String(error ?? '').trim().replace(/[\s.]+$/, '')
  if (!said) return 'Not applied. It still waits.'
  const stop = /[!?]$/.test(said) ? '' : '.'
  if (learning && /\bstill (waits|pending)\b/i.test(said)) return `${said}${stop}`
  return `Not applied: ${said}${stop} It still waits.`
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
  // A learning row also by its card's variant: a template and a lesson are never one batch.
  const card = (it.refs as Record<string, unknown> | undefined)?.card as { variant?: unknown } | undefined
  const variant = card && typeof card.variant === 'string' ? `|${card.variant}` : ''
  return `${p?.provenance ?? ''}|${it.item_kind ?? ''}${variant}`
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
  // A proposal whose Accept can't be done here is never in a batch: its row offers no Approve.
  return selected.every((it) => groupKey(it) === first && acceptOf(it)?.effect !== 'none')
}

/** Distinct group count — what the disabled control explains to the user. */
export function groupCount(selected: Array<Pick<InboxItem, 'refs' | 'item_kind'>>): number {
  return new Set(selected.map(groupKey)).size
}
