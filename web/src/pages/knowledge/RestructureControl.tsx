import { useCallback, useEffect, useState } from 'react'
import { Scissors, AlertTriangle, Undo2, Link2 } from 'lucide-react'
import {
  api, ApiError,
  type KnowledgeItem, type KnowledgeSection, type KnowledgeDuplicate,
  type KnowledgeRestructurePlan, type KnowledgeRestructureResult,
} from '../../lib/api'
import { Button } from '../../ui/Button'
import { Modal } from '../../ui/Modal'
import { Checkbox, Field, Select, TextInput } from '../../ui/forms'
import { InlineError } from '../../ui/InlineError'

/** A rejection's human sentence. `ApiError.message` is already the server's `error.message`
 *  (extracted by the shared `errText` when the response was read), so there is nothing to
 *  re-parse here — and `errText` itself takes a `Response`, not a caught error. */
function msg(e: unknown): string {
  return e instanceof Error && e.message ? e.message : 'the restructure could not be completed'
}

/** KL-19 — the structural editing verbs, on the surface where the need is felt.
 *
 *  This lives in the READING rail, not on a management screen, and that placement is the point:
 *  a reader notices that one note is really three while reading it, and a verb they have to go
 *  find somewhere else is one they will not use. The trigger sits beside Find and Highlight —
 *  the two other things a reader does to a document they are in the middle of.
 *
 *  **Two phases, always.** Choosing a verb and filling its arguments PREVIEWS; nothing is written
 *  until the reader has seen what it would do and what it would break. The preview's `token` is
 *  what the confirm echoes, and the server refuses a token whose plan has moved — so the panel
 *  cannot skip the preview even by accident. The token also makes a doubled submit safe: the
 *  server replays the first result rather than restructuring twice, which is why the Apply button
 *  needs no click-guard of its own beyond its loading state.
 *
 *  **The blast radius is server-composed, never a static sentence.** `plan.summary` and each
 *  `break.message` are computed from the actual rows — "10 words move to “Eviction policy”",
 *  "3 citations name the folded-in copy". A hand-written "This cannot be undone" would be both
 *  vaguer and, here, false: every verb IS undoable, and the panel offers the undo inline the
 *  moment it finishes rather than hiding it in a toast that scrolls away.
 */

type Verb = 'split' | 'extract' | 'merge' | 'retitle' | 'move' | 'change_kind'

const VERB_LABELS: { value: Verb; label: string }[] = [
  { value: 'split', label: 'Split at a section boundary' },
  { value: 'extract', label: 'Extract the selected passage' },
  { value: 'merge', label: 'Merge a near-duplicate into this' },
  { value: 'retitle', label: 'Rename and relink' },
  { value: 'move', label: 'Change tags' },
  { value: 'change_kind', label: "Change this item's kind" },
]

export function RestructureControl({ item, selection, onDone }: {
  item: KnowledgeItem
  /** The reader's current selection, so Extract can act on the passage they highlighted. */
  selection?: string
  /** Called after a verb applies OR is undone — both change the item under the reader. */
  onDone: () => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button size="sm" variant="ghost" ariaExpanded={open} onClick={() => setOpen(true)}>
        <Scissors size={14} /> Restructure
      </Button>
      {open && (
        <Modal title={`Restructure “${item.title || 'Untitled'}”`} icon={<Scissors size={18} />}
          onClose={() => setOpen(false)}>
          <RestructureForm item={item} selection={selection} onDone={onDone} />
        </Modal>
      )}
    </>
  )
}

function RestructureForm({ item, selection, onDone }: {
  item: KnowledgeItem
  selection?: string
  onDone: () => void
}) {
  const [verb, setVerb] = useState<Verb>('split')
  const [sections, setSections] = useState<KnowledgeSection[] | null>(null)
  const [duplicates, setDuplicates] = useState<KnowledgeDuplicate[] | null>(null)
  const [chosen, setChosen] = useState<number[]>([])
  const [title, setTitle] = useState('')
  const [kind, setKind] = useState(item.kind || 'fact')
  const [tags, setTags] = useState((item.tags || []).join(', '))
  const [mergeId, setMergeId] = useState('')
  const [relink, setRelink] = useState(true)
  const [plan, setPlan] = useState<KnowledgeRestructurePlan | null>(null)
  const [done, setDone] = useState<KnowledgeRestructureResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // 🔴 Both lookups store their REJECTION rather than falling back to an empty list. An empty
  // sections list legitimately means "no headings, cannot be split", and an empty duplicates list
  // legitimately means "nothing to merge" — so a swallowed failure would render as the verb being
  // unavailable, which is indistinguishable from the truth and permanent.
  useEffect(() => {
    let live = true
    api.knowledgeItemSections(item.id)
      .then((d) => { if (live) setSections(d.sections) })
      .catch((e) => { if (live) { setSections([]); setErr(msg(e)) } })
    api.knowledgeDuplicates(item.id)
      .then((d) => { if (live) setDuplicates(d) })
      .catch(() => { if (live) setDuplicates([]) })
    return () => { live = false }
  }, [item.id])

  // Extract acts on the reader's selection, located in the body by text rather than by a stored
  // offset — the selection came from the RENDERED article, and the only thing both sides agree on
  // is the string itself.
  const spanStart = selection ? (item.content || '').indexOf(selection) : -1
  const params = useCallback((): Record<string, unknown> => {
    switch (verb) {
      case 'split': return { offsets: chosen }
      case 'extract': return { start: spanStart, end: spanStart + (selection || '').length, title }
      case 'merge': return { merge_id: mergeId }
      case 'retitle': return { title }
      case 'move': return { tags: tags.split(',').map((t) => t.trim()).filter(Boolean) }
      case 'change_kind': return { kind }
    }
  }, [verb, chosen, spanStart, selection, title, mergeId, tags, kind])

  const reset = () => { setPlan(null); setDone(null); setErr('') }

  const preview = async () => {
    setBusy(true); setErr(''); setPlan(null)
    try {
      const res = await api.knowledgeRestructurePreview(item.id, verb, params())
      setPlan(res.plan)
      // Deliberately NOT `setRelink(res.plan.relink_offered)`. That conflates "the offer is
      // shown" with "the offer is accepted": a plan with nothing to relink would arrive with the
      // flag off and the request would then say `relink: false` — harmless today, since there is
      // nothing to repair, but it means the wire says the reader declined a repair they were
      // never offered. Repairing by default is the safe reading; the checkbox below is the only
      // thing that turns it off.
    } catch (e) {
      setErr(msg(e))
    } finally {
      setBusy(false)
    }
  }

  const apply = async () => {
    if (!plan) return
    setBusy(true); setErr('')
    try {
      const res = await api.knowledgeRestructureApply(item.id, verb, params(), plan.token, relink)
      setDone(res)
      setPlan(null)
      onDone()
    } catch (e) {
      // The server attaches the FRESH plan to its 409, but `ApiError` carries only a status and a
      // message — widening that shared primitive to hold a body is a change every caller in the app
      // inherits, and not this atom's to make. So a stale preview costs one extra round trip and
      // re-renders as the NEW preview rather than as an error the reader must interpret and retry
      // by hand. The refusal itself is what matters: nothing was applied.
      setErr(msg(e))
      if (e instanceof ApiError && e.status === 409) {
        setPlan(null)
        await preview()
        setErr('This item changed since the preview — review the updated one below.')
      }
    } finally {
      setBusy(false)
    }
  }

  const undo = async () => {
    if (!done) return
    setBusy(true); setErr('')
    try {
      await api.knowledgeRestructureUndo(done.undo_token)
      setDone(null)
      onDone()
    } catch (e) {
      setErr(msg(e))
    } finally {
      setBusy(false)
    }
  }

  const ready = readiness(verb, { chosen, spanStart, title, mergeId, sections, duplicates })

  if (done) {
    return (
      <div className="flex flex-col gap-m">
        <p data-type="body-m" className="text-on-surface">{done.summary}</p>
        <Consequences result={done} />
        {err && <InlineError onDismiss={() => setErr('')}>{err}</InlineError>}
        {/* The undo is offered HERE, inline, the moment the verb lands. A destructive restructure
            whose reversal lives in a toast that has already faded is one a reader will not risk. */}
        <div className="flex items-center gap-s">
          <Button size="sm" variant="tonal" loading={busy} onClick={undo}>
            <Undo2 size={14} /> Undo this restructure
          </Button>
          <Button size="sm" variant="ghost" onClick={reset}>Restructure again</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-l">
      <Field label="Verb" hint="Every one of these is previewed before it is applied, and undoable after.">
        <Select value={verb} options={VERB_LABELS} ariaLabel="Restructure verb"
          onChange={(v) => { setVerb(v as Verb); reset() }} />
      </Field>

      {verb === 'split' && (
        <SectionPicker sections={sections} chosen={chosen}
          onToggle={(offset) => {
            setPlan(null)
            setChosen((c) => (c.includes(offset) ? c.filter((o) => o !== offset) : [...c, offset]))
          }} />
      )}

      {verb === 'extract' && (
        <Field label="New item's title"
          hint={selection
            ? `${(selection || '').split(/\s+/).filter(Boolean).length} selected words move into it`
            : 'Select a passage in the article first — extract acts on what you highlighted'}>
          <TextInput value={title} onChange={(v) => { setTitle(v); setPlan(null) }}
            ariaLabel="Title for the extracted item" placeholder="What this passage is about" />
        </Field>
      )}

      {verb === 'merge' && (duplicates?.length ? (
        <Field label="Near-duplicate to fold in"
          hint="This item survives; the one you pick is deleted after its tags, shelves, highlights and relations move across">
          {/* Each option carries the SCORER's own reason, not just a title. A merge deletes an
              item, and "Caching notes" twice over is not a choice a reader can make — the reason
              is what makes the destructive option reviewable. */}
          <Select value={mergeId} ariaLabel="Item to fold into this one"
            options={[
              { value: '', label: 'Choose an item…' },
              ...duplicates.map((d) => ({ value: d.id, label: `${d.title} — ${d.reason}` })),
            ]}
            onChange={(v) => { setMergeId(v); setPlan(null) }} />
        </Field>
      ) : (
        /* No picker at all rather than a DISABLED one. `Select` takes no `disabledReason`, so a
           disabled dropdown is a control whose unavailability nothing states — the global
           disabled-reason census flags exactly that, and it is right to: an empty greyed-out
           dropdown reading "Nothing to merge" is worse than the sentence on its own. */
        <p data-type="body-s" className="text-on-surface-low">
          No near-duplicates were found for this item, so there is nothing to fold into it.
        </p>
      ))}

      {verb === 'retitle' && (
        <Field label="New title"
          hint="Wikilinks in other items that name the old title are relinked, and the item's logical identity is re-derived">
          <TextInput value={title} onChange={(v) => { setTitle(v); setPlan(null) }}
            ariaLabel="New title" placeholder={item.title} />
        </Field>
      )}

      {verb === 'move' && (
        <Field label="Tags" hint="Comma separated. This replaces the item's tags rather than adding to them.">
          <TextInput value={tags} onChange={(v) => { setTags(v); setPlan(null) }}
            ariaLabel="Tags, comma separated" placeholder="infra, performance" />
        </Field>
      )}

      {verb === 'change_kind' && (
        /* A free-text field rather than a Select, deliberately. The kind vocabulary is
           `semantics.KINDS` in the backend and no endpoint publishes it, so a local option list
           would be a second copy that silently goes stale the first time a kind is added — and
           an incomplete picker cannot express a kind the store accepts. The server refuses an
           unknown kind by NAMING the whole valid set, so the vocabulary reaches the reader from
           its one owner instead of from a duplicate. */
        <Field label="Kind"
          hint="The item's semantic kind, which also re-derives its logical identity. An unrecognised kind is refused with the list of valid ones.">
          <TextInput value={kind} onChange={(v) => { setKind(v); setPlan(null) }}
            ariaLabel="Item kind" placeholder="fact, decision, insight, reference, …" />
        </Field>
      )}

      {err && <InlineError onDismiss={() => setErr('')}>{err}</InlineError>}

      {plan && <PlanReview plan={plan} relink={relink} onRelink={setRelink} />}

      <div className="flex items-center gap-s">
        {!plan && (
          <Button size="sm" variant="tonal" loading={busy}
            disabled={!!ready} disabledReason={ready || undefined} onClick={preview}>
            Preview the change
          </Button>
        )}
        {plan && (
          <>
            {/* `danger` on the verbs that delete or rewrite a body. The button's own copy names the
                act, and the review above it names the consequence in the server's own numbers. */}
            <Button size="sm" variant={destructive(plan.verb) ? 'danger' : 'primary'}
              loading={busy} onClick={apply}>
              {applyLabel(plan.verb)}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setPlan(null)}>Back</Button>
          </>
        )}
      </div>
    </div>
  )
}

/** Why the Preview button is unavailable, or '' when it is ready. */
function readiness(verb: Verb, s: {
  chosen: number[]; spanStart: number; title: string; mergeId: string
  sections: KnowledgeSection[] | null; duplicates: KnowledgeDuplicate[] | null
}): string {
  if (verb === 'split') {
    if (s.sections && s.sections.length === 0) return 'This item has no headings to split on'
    if (!s.chosen.length) return 'Choose at least one section to split off'
  }
  if (verb === 'extract') {
    if (s.spanStart < 0) return 'Select a passage in the article first'
    if (!s.title.trim()) return 'The extracted item needs a title'
  }
  if (verb === 'merge' && !s.mergeId) return 'Choose the item to fold in'
  if (verb === 'retitle' && !s.title.trim()) return 'Enter the new title'
  return ''
}

function destructive(verb: string): boolean {
  // Deletes an item, or rewrites a body so a passage leaves it. `move`, `retitle` and
  // `change_kind` change metadata and are not treated as destructive — dressing every verb in
  // danger styling would make the styling mean nothing on the two that earn it.
  return verb === 'merge' || verb === 'split' || verb === 'extract'
}

function applyLabel(verb: string): string {
  if (verb === 'merge') return 'Merge and delete the copy'
  if (verb === 'split') return 'Split this item'
  if (verb === 'extract') return 'Extract the passage'
  return 'Apply'
}

function SectionPicker({ sections, chosen, onToggle }: {
  sections: KnowledgeSection[] | null
  chosen: number[]
  onToggle: (offset: number) => void
}) {
  if (sections === null) return <p data-type="body-s" className="text-on-surface-low">Reading the outline…</p>
  if (!sections.length) {
    return (
      <p data-type="body-s" className="text-on-surface-low">
        This item has no headings, so there is no section boundary to split on. Extract a selected
        passage instead.
      </p>
    )
  }
  return (
    <Field label="Sections to split off"
      hint="Each one you choose becomes its own item, linked back to this one and keeping its tags and shelves">
      <ul className="flex flex-col gap-xs">
        {sections.map((s) => (
          <li key={s.offset} className="flex items-center gap-s">
            <Checkbox checked={chosen.includes(s.offset)} onChange={() => onToggle(s.offset)}
              ariaLabel={`Split off the section “${s.title || 'Untitled'}”`} />
            <span data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface">
              {s.title || 'Untitled section'}
            </span>
            <span data-type="label-s" className="shrink-0 text-on-surface-low">line {s.line}</span>
          </li>
        ))}
      </ul>
    </Field>
  )
}

function PlanReview({ plan, relink, onRelink }: {
  plan: KnowledgeRestructurePlan
  relink: boolean
  onRelink: (v: boolean) => void
}) {
  return (
    <div className="squircle flex flex-col gap-m bg-surface-container p-l">
      <p data-type="body-m" className="text-on-surface">{plan.summary}</p>
      {plan.breaks.length > 0 && (
        <ul className="flex flex-col gap-s">
          {plan.breaks.map((b, i) => (
            <li key={`${b.kind}-${i}`} className="flex items-start gap-s">
              {b.relinkable
                ? <Link2 size={14} className="mt-0.5 shrink-0 text-on-surface-low" aria-hidden="true" />
                : <AlertTriangle size={14} className="mt-0.5 shrink-0 text-danger" aria-hidden="true" />}
              <span data-type="body-s" className={b.relinkable ? 'text-on-surface' : 'text-danger'}>
                {b.message}
              </span>
            </li>
          ))}
        </ul>
      )}
      {plan.relink_offered && (
        <label className="flex items-center gap-s">
          <Checkbox checked={relink} onChange={onRelink}
            ariaLabel="Relink the references this change would break" />
          <span data-type="body-s" className="text-on-surface">
            Relink the references above — unticked, they simply break
          </span>
        </label>
      )}
      {plan.breaks.length === 0 && (
        <p data-type="body-s" className="text-on-surface-low">
          Nothing points at this item in a way this change would break.
        </p>
      )}
    </div>
  )
}

/** What the verb actually did, in its own numbers — never a bare "Done". */
function Consequences({ result }: { result: KnowledgeRestructureResult }) {
  const lines: string[] = []
  if (result.created?.length) lines.push(`${result.created.length} new item(s) created and linked back`)
  if (result.annotations_moved) lines.push(`${result.annotations_moved} highlight(s) followed their text`)
  if (result.citations_widened) lines.push(`${result.citations_widened} citation(s) widened to cite the whole item`)
  if (result.wikilinks_relinked?.links) lines.push(`${result.wikilinks_relinked.links} wikilink(s) relinked`)
  if (result.moved?.relations) lines.push(`${result.moved.relations} relation(s) moved to the survivor`)
  if (result.moved?.citations) lines.push(`${result.moved.citations} citation(s) repointed`)
  if (result.logical_key) lines.push(`Logical identity is now ${result.logical_key}`)
  if (!lines.length) return null
  return (
    <ul className="flex flex-col gap-xs">
      {lines.map((line) => (
        <li key={line} data-type="body-s" className="text-on-surface-low">{line}</li>
      ))}
    </ul>
  )
}
