import { useState } from 'react'
import { ExternalLink, GitMerge } from 'lucide-react'
import { Button } from '../../ui/Button'
import { FieldError } from '../../ui/forms'
import { confirm } from '../../ui/dialog'
import { notify } from '../../app/appSdk'
import { api, type KnowledgeDuplicate } from '../../lib/api'
import { relPast } from '../schedule/scheduleMeta'
import { fvs } from '../../design/fontWeight'

/** The near-duplicate candidates for one knowledge item, with the merge action
 *  (KNOWLEDGE-LIBRARY S3, T3.2 — the frontend half of `find_duplicates`/`merge_items`).
 *
 *  🔑 THE SURVIVOR IS THE ITEM YOU ARE LOOKING AT, always — never the row you click. The route
 *  puts the survivor in the PATH and the loser in the body for exactly this reason, and the UI
 *  keeps that asymmetry visible instead of offering a per-row direction picker: two destructive
 *  buttons on one row, differing only in which of two similarly-titled documents dies, is how a
 *  merge UI destroys the copy the user meant to keep. Wanting the other direction is a real need
 *  and it has an answer — open the other item and merge from there — so the confirmation SAYS so
 *  rather than leaving the user to guess.
 *
 *  🪤 AN EMPTY LIST AND A FAILED LOOKUP ARE DIFFERENT ANSWERS, and here the distinction is
 *  sharper than on any list surface in the app: "no duplicates" is the correct answer for almost
 *  every item, so a swallowed rejection is indistinguishable from the truth — permanently, and on
 *  the one surface whose entire job is to tell you a second copy exists. `api.knowledgeDuplicates`
 *  therefore carries no `.catch(() => [])` (see its comment), the page threads the rejection here,
 *  and this component renders it as a failure with a retry. The section is mounted by its host
 *  whenever there are candidates OR the lookup failed, so the failure cannot hide behind absence.
 */
export function DuplicateList({ item, duplicates, error, onRetry, onOpenItem, onMerged }: {
  /** The SURVIVOR — the item this panel belongs to. Its title names the keeper in the dialog. */
  item: { id: string; title?: string }
  duplicates: KnowledgeDuplicate[]
  /** The duplicates lookup's rejection, when it failed. `null`/undefined = it answered. */
  error?: unknown
  onRetry: () => void
  onOpenItem: (id: string) => void
  /** A merge landed — the host re-reads the item (it just inherited rows) and this list. */
  onMerged: () => void
}) {
  // Per-row, not per-list: merging one candidate must not disable the others' buttons, and the
  // spinner belongs on the row whose item is being deleted.
  const [busy, setBusy] = useState<string | null>(null)

  if (error) {
    return (
      <div className="flex flex-col items-start gap-1.5">
        <FieldError>
          {error instanceof Error && error.message
            ? `Couldn't check for duplicates: ${error.message}`
            : "Couldn't check for duplicates."}
        </FieldError>
        {/* Deliberately NOT "no duplicates found". The lookup did not answer, so this surface
            knows nothing about whether a second copy exists and must not imply that it does. */}
        <p className="text-on-surface-low text-[0.75rem]">This item may still have duplicates.</p>
        <Button variant="secondary" size="xs" onClick={onRetry}>Try again</Button>
      </div>
    )
  }

  async function merge(dup: KnowledgeDuplicate) {
    const keeper = item.title || 'this item'
    const loser = dup.title || 'the other copy'
    // 🔑 THE TWO COPIES ARE NAMED BY POSITION, NOT BY TITLE, and that is the whole reason this
    // copy reads the way it does. `find_duplicates` requires title similarity ≥ 0.85, so a
    // candidate very nearly always shares the survivor's title — and in the common case shares it
    // exactly. Measured by driving a real pair: the dialog read *Merge “Rust async book notes”
    // into “Rust async book notes”?*, which names neither copy. So the survivor is identified as
    // "the item you have open" (unambiguous by construction) and the loser is identified by the
    // metadata that actually differs.
    const dupMeta = [
      dup.word_count > 0 ? `${dup.word_count.toLocaleString()} words` : '',
      dup.created_at ? `added ${relPast(dup.created_at)}` : '',
    ].filter(Boolean).join(', ')
    // The dialog names the four things a user needs before agreeing to an irreversible delete:
    // which copy SURVIVES, what it INHERITS, which copy is DELETED, and how to get the opposite
    // outcome. `confirmDelete`'s "This cannot be undone." is folded in verbatim — same class of act.
    const ok = await confirm({
      title: `Merge this duplicate into “${keeper}”?`,
      body: (
        <>
          <p>
            <strong>The item you have open is kept</strong> and inherits everything from the
            duplicate — its collections, tags, entity mentions and highlights.
          </p>
          <p className="mt-2">
            <strong>The duplicate is then deleted:</strong> “{loser}”
            {dupMeta && <> — {dupMeta}</>}. This cannot be undone.
          </p>
          <p className="mt-2 text-on-surface-low">
            To keep that copy instead, open it and merge from there.
          </p>
        </>
      ),
      danger: true,
      confirmLabel: 'Merge and delete',
    })
    if (!ok) return
    setBusy(dup.id)
    try {
      const res = await api.mergeKnowledgeItems(item.id, dup.id)
      // Report what MOVED, not a bare "Merged". The inheritance is the substance of the
      // operation — a user who just deleted a document deserves to see that its curation
      // arrived rather than having to go looking for it.
      const parts = [
        [res.moved?.collections ?? 0, 'collection'],
        [res.moved?.tags ?? 0, 'tag'],
        [res.moved?.mentions ?? 0, 'mention'],
        [res.moved?.annotations ?? 0, 'highlight'],
      ] as const
      const moved = parts
        .filter(([n]) => n > 0)
        .map(([n, noun]) => `${n} ${noun}${n === 1 ? '' : 's'}`)
      notify(
        moved.length
          ? `Merged “${loser}” in — ${moved.join(', ')} moved across`
          : `Merged “${loser}” in`,
        'success',
      )
      onMerged()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'That merge did not go through', 'error')
    } finally {
      setBusy(null)
    }
  }

  return (
    <ul className="flex flex-col gap-1.5">
      {duplicates.map((dup) => (
        <li key={dup.id} className="rounded-md bg-surface-container px-m py-2">
          {/* The title is TEXT, not a button, and that is a primitive-adoption constraint rather
              than a preference: `Button` is `whitespace-nowrap` by contract (a labelled pill must
              never wrap), so a document title in a resizable side panel cannot live inside one —
              and a hand-rolled bespoke button element here is exactly what the primitive-adoption
              ratchet exists to stop. (Written out rather than shown as markup on purpose: that
              ratchet counts the literal tag TEXTUALLY, comments included, so quoting it here
              would trip the very rail this comment is explaining.)
              Opening the candidate is therefore its own primitive control below, which also drops
              the nested-interactive shape a clickable row containing a Merge button would have. */}
          <p className="truncate text-on-surface text-[0.8125rem]" style={fvs(500)}>
            {dup.title || '(untitled)'}
          </p>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-on-surface-low text-[0.75rem]">
            {dup.word_count > 0 && <span>{dup.word_count.toLocaleString()} words</span>}
            {dup.word_count > 0 && dup.created_at && <span aria-hidden>·</span>}
            {dup.created_at && <span>added {relPast(dup.created_at)}</span>}
          </div>
          {/* The scorer's own account of the match. Without it a merge button asks the user to
              destroy a document on the app's unexplained word — `find_duplicates` returns this
              precisely so the claim is reviewable. */}
          {dup.reason && (
            <p className="mt-1 text-on-surface-var text-[0.75rem]">{dup.reason}</p>
          )}
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {/* Inspect BEFORE destroy. The two similarly-titled copies are the whole problem, so
                reading the other one has to be one click away from the button that deletes it.
                `ariaLabel` names WHICH item — four rows of "Open" are four identical names. */}
            <Button variant="ghost" size="xs" onClick={() => onOpenItem(dup.id)}
              ariaLabel={`Open “${dup.title || 'untitled item'}”`}>
              <ExternalLink size={14} aria-hidden />
              Open
            </Button>
            {/* `loading` (not a hand-rolled spinner): the primitive already cross-fades the label
                to an aria-hidden spinner AND sets `aria-busy`, and it honours reduced motion
                internally — a second spinner in the children would render behind that one. */}
            <Button variant="danger" size="xs" loading={busy === dup.id}
              disabled={busy !== null} onClick={() => merge(dup)}>
              <GitMerge size={14} aria-hidden />
              {/* The full direction is in the label, not only in the dialog: a row action that
                  says just "Merge" leaves which document dies to be inferred from layout. */}
              Merge into this item
            </Button>
          </div>
        </li>
      ))}
    </ul>
  )
}
