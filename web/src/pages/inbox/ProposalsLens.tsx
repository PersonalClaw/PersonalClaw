import { useMemo, useState } from 'react'
import { Check, Pencil, Lightbulb, AlertTriangle } from 'lucide-react'
import { fvs } from '../../design/fontWeight'
import { Button } from '../../ui/Button'
import { Checkbox, FieldError, TextArea } from '../../ui/forms'
import { EmptyState } from '../../ui/ListScaffold'
import { api, type InboxItem, type InboxProposal } from '../../lib/api'
import {
  APPLY_CASE_LABEL,
  applyCase,
  canBatchApprove,
  groupCount,
  groupLabel,
  proposalOf,
} from './proposalLens'

/** Per-item apply outcome, kept so a BATCH shows N individual results — one failure must
 *  not read as "the batch failed", and a success next to it must stay visible. */
type Outcome = { ok: boolean; error?: string }

/**
 * The Proposals lens (INU-7 T7.3).
 *
 * Three properties, each a deliberate choice rather than a default:
 *
 * 1. **A mixed sweep is impossible, not discouraged.** Batch-approve is enabled only when
 *    every selected row shares one `(provenance, item_kind)` group. Selecting a learning
 *    proposal and an app proposal together leaves the control unavailable, and it SAYS why
 *    (`disabledReason` — aria-disabled + focusable, so a keyboard user hears the reason
 *    instead of tabbing past a silent dead button).
 * 2. **A batch is N applies with N outcomes.** Each row's result is rendered against that
 *    row. Nothing is rolled back, because each apply already ran through its own dispatcher.
 * 3. **Edit-then-approve edits what apply RECEIVES.** The editor holds the `apply` payload,
 *    not the human-readable preview: editing prose the dispatcher ignores would be an inert
 *    control that looks like it changed the outcome.
 */
export function ProposalsLens({ items, onChanged }: { items: InboxItem[]; onChanged: () => void }) {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({})
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [draftError, setDraftError] = useState('')
  const [busy, setBusy] = useState(false)

  const selectedItems = useMemo(
    () => items.filter((it) => selected.has(it.id)),
    [items, selected],
  )
  const batchOk = canBatchApprove(selectedItems)
  const groups = groupCount(selectedItems)

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function applyOne(it: InboxItem, edited?: InboxProposal) {
    const res = await api
      .applyInboxProposal(it.id, edited)
      .catch((e: unknown) => ({ ok: false, error: e instanceof Error ? e.message : 'apply failed' }))
    setOutcomes((prev) => ({ ...prev, [it.id]: { ok: !!res.ok, error: res.error } }))
    return !!res.ok
  }

  async function approve(it: InboxItem) {
    setBusy(true)
    try {
      await applyOne(it)
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  /** N individual applies, sequential so per-item outcomes land in list order. */
  async function approveSelected() {
    if (!batchOk) return
    setBusy(true)
    try {
      for (const it of selectedItems) await applyOne(it)
      setSelected(new Set())
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  function startEdit(it: InboxItem, p: InboxProposal) {
    setEditing(it.id)
    setDraftError('')
    setDraft(JSON.stringify(p.apply, null, 2))
  }

  async function saveEdit(it: InboxItem, p: InboxProposal) {
    let parsed: unknown
    try {
      parsed = JSON.parse(draft)
    } catch {
      setDraftError('Not valid JSON — fix it before approving.')
      return
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setDraftError('The apply payload must be an object.')
      return
    }
    setBusy(true)
    try {
      await applyOne(it, { ...p, apply: parsed as InboxProposal['apply'] })
      setEditing(null)
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  if (items.length === 0) {
    return (
      <EmptyState
        icon={Lightbulb}
        title="No proposals"
        hint="When a skill, a workflow or an app suggests a change, it lands here for you to approve."
      />
    )
  }

  return (
    <div className="flex flex-col gap-m">
      <div className="flex items-center gap-s">
        <Button
          size="sm"
          variant="primary"
          onClick={approveSelected}
          loading={busy}
          disabledReason={
            selectedItems.length === 0
              ? 'Select one or more proposals first'
              : !batchOk
                ? `Selection spans ${groups} different sources or kinds — approve one group at a time`
                : undefined
          }
          disabled={selectedItems.length === 0 || !batchOk}
        >
          <Check size={14} />
          {batchOk && selectedItems.length > 0
            ? `Approve ${selectedItems.length} from ${groupLabel(selectedItems[0])}`
            : 'Approve selected'}
        </Button>
        <span className="text-on-surface-low text-[0.8125rem]">
          {selectedItems.length > 0
            ? `${selectedItems.length} selected`
            : 'Batch approve works within one source and kind.'}
        </span>
      </div>

      <div className="flex flex-col gap-s">
        {items.map((it) => {
          const p = proposalOf(it)
          const kase = p ? applyCase(p) : ''
          const outcome = outcomes[it.id]
          return (
            <div
              key={it.id}
              className="rounded-lg px-m py-m"
              style={{ background: 'var(--color-surface-container)' }}
            >
              <div className="flex items-start gap-s">
                <Checkbox
                  checked={selected.has(it.id)}
                  onChange={() => toggle(it.id)}
                  ariaLabel={`Select proposal: ${p?.title || it.message}`}
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-on-surface text-[0.9375rem]" style={fvs(500)}>
                    {p?.title || it.message}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-s text-on-surface-low text-[0.75rem]">
                    <span>{groupLabel(it)}</span>
                    {kase ? (
                      <span>· {APPLY_CASE_LABEL[kase]}</span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-warn">
                        <AlertTriangle size={11} /> no runnable action
                      </span>
                    )}
                  </div>
                  {p?.preview && (
                    <pre
                      className={`mt-s max-h-40 overflow-auto whitespace-pre-wrap text-on-surface-var text-[0.8125rem] ${p.preview_kind === 'diff' ? 'font-mono' : ''}`}
                    >
                      {p.preview}
                    </pre>
                  )}
                  {/* ONE element that is both the visible outcome and the announcement, ALWAYS
                      MOUNTED and `sr-only` (so it costs no layout) until an outcome lands. It was
                      previously `{outcome && <div role="status">}` — created at the same moment its
                      text appeared, which `ResultAnnouncement` records as not reliably observed
                      ("Always MOUNTED, rendered empty when idle").
                      Deliberately NOT a hidden region plus a visible copy: duplicating the sentence
                      would announce it twice and would make `getByText` ambiguous for the row's own
                      tests. One node, one sentence, announced and seen. */}
                  <div
                    role="status"
                    aria-live="polite"
                    className={outcome
                      ? `mt-s text-[0.8125rem] ${outcome.ok ? 'text-ok' : 'text-danger'}`
                      : 'sr-only'}
                  >
                    {outcome ? (outcome.ok ? 'Applied.' : `Not applied — ${outcome.error}. Still pending.`) : ''}
                  </div>
                  {editing === it.id && p && (
                    <div className="mt-s flex flex-col gap-s">
                      <TextArea
                        value={draft}
                        onChange={setDraft}
                        rows={6}
                        mono
                        size="sm"
                        ariaLabel="Apply payload (JSON)"
                      />
                      {/* `FieldError` (43 uses) carries role="alert": a rejected edit is unrequested
                          bad news and must interrupt, which a plain div never does. It also uses the
                          `text-danger` token the other 101 call sites use, rather than the
                          `text-error` alias used in only 2 files. */}
                      {draftError && <FieldError>{draftError}</FieldError>}
                      <div className="flex gap-s">
                        <Button size="sm" variant="primary" onClick={() => saveEdit(it, p)} loading={busy}>
                          <Check size={14} /> Approve edited
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
                          Cancel
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
                {editing !== it.id && (
                  <div className="flex shrink-0 items-center gap-s">
                    {p?.editable && (
                      <Button size="sm" variant="secondary" onClick={() => startEdit(it, p)}>
                        <Pencil size={14} /> Edit
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="primary"
                      onClick={() => approve(it)}
                      loading={busy}
                      disabled={!kase}
                      disabledReason={!kase ? 'This proposal declares no runnable action' : undefined}
                    >
                      <Check size={14} /> Approve
                    </Button>
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
