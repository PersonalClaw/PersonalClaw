import { useEffect, useState } from 'react'
import { AlertTriangle, Scale } from 'lucide-react'
import { api, type KnowledgeConflict } from '../../lib/api'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { fvs } from '../../design/fontWeight'
import { accentChip } from '../../design/accent'

/** Recorded contradictions in the knowledge store (KNOWLEDGE-SYNTHESIS §3.2).
 *
 *  Read-only, deliberately. Conflicts are flagged when a claim is WRITTEN, not when it is
 *  read — by the time a contradiction surfaces during retrieval something has already cited
 *  one side of it. Both claims are always kept, so this surface exists to make the flag
 *  visible rather than to settle it: deciding which source to trust is a judgement about the
 *  sources, which is the owner's to make. A "resolve" button here would invite the system to
 *  discard evidence, and a discarded claim is unrecoverable.
 *
 *  Every row is a proven conflict — two claims that provably cannot both hold — so the header
 *  states that flatly instead of grading trust per row. `prefer` shows the source-precedence
 *  ladder's advice, and shows nothing when the ladder cannot decide — two same-tier sources
 *  genuinely have no winner, and inventing one would manufacture authority out of arrival
 *  order. */
export function ConflictPanel() {
  const [conflicts, setConflicts] = useState<KnowledgeConflict[] | null>(null)
  // #532: the rejection used to become `[]`, and the panel then rendered "No contradictions
  // recorded" — the single most load-bearing claim this surface makes, asserted out of a failed
  // read. `null` already means "not loaded", so a substitute here can only ever lie.
  const [err, setErr] = useState<unknown>(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let alive = true
    api.knowledgeConflicts()
      .then((d) => { if (alive) setConflicts(d.conflicts) })
      .catch((e) => { if (alive) setErr(e) })
    return () => { alive = false }
  }, [reloadKey])

  // Error BEFORE the skeleton: on failure `conflicts` stays `null`, so the loading test below is
  // also true and would spin forever if it came first.
  if (err !== null) {
    return <LoadError what="contradictions" error={err} onRetry={() => { setErr(null); setReloadKey((k) => k + 1) }} />
  }
  if (conflicts === null) return <ListSkeleton rows={3} what="contradictions" />
  if (conflicts.length === 0) {
    return (
      <EmptyState
        icon={Scale}
        title="No contradictions recorded"
        hint="When two stored claims disagree about the same subject, both are kept and the disagreement shows up here."
      />
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {conflicts.map((c, i) => (
        <ConflictRow key={`${c.item_id}-${i}`} conflict={c} />
      ))}
    </div>
  )
}

function ConflictRow({ conflict }: { conflict: KnowledgeConflict }) {
  return (
    <div data-type="body-s" className="rounded-lg border border-outline-variant bg-surface p-3">
      <div data-type="caption" className="mb-2 flex items-center gap-2 text-on-surface-low">
        {/* Unconditional, because every recorded conflict is proven: the store's only writer is
            the deterministic tier. No confidence percentage rides along for the same reason —
            printing "100%" on a proof implies a scale it is not measured on. */}
        <AlertTriangle size={13} className="text-warning" aria-hidden />
        <span style={fvs(600)}>Provable conflict</span>
        <span aria-hidden>·</span>
        <span>{conflict.kind}</span>
      </div>

      <ClaimSide
        text={conflict.left_claim}
        preferred={conflict.prefer === 'left'}
        label={conflict.item_title || conflict.left_item}
      />
      <div data-type="caption" className="my-1 pl-3 text-on-surface-low">versus</div>
      <ClaimSide
        text={conflict.right_claim}
        preferred={conflict.prefer === 'right'}
        label={conflict.right_item}
      />

      {conflict.detail && (
        <div data-type="caption" className="mt-2 text-on-surface-low">{conflict.detail}</div>
      )}
      {conflict.prefer === '' && (
        <div data-type="caption" className="mt-2 text-on-surface-low">
          Both sources carry the same weight — this one needs a human call.
        </div>
      )}
    </div>
  )
}

function ClaimSide(
  { text, preferred, label }: { text: string; preferred: boolean; label: string },
) {
  return (
    <div className="flex items-start gap-2">
      <div className={`min-w-0 flex-1 ${preferred ? '' : 'text-on-surface-low'}`}>
        <div style={fvs(preferred ? 600 : 400)}>{text}</div>
        {/* 🪤 THIS LABEL IS THE ONLY THING NAMING THE SOURCE OF A CLAIM, and it is the label a reader
            needs most: the panel exists to ask which of two sources to trust, so "which document said
            this" is the question. Measured at 390px on real conflicts, it clips with `title: null` —
            203px of the 369px it needs on one side, 332px of 369px on the other — while at 1440px
            nothing clips (1057-1186px available). `title` is the app's idiom for a truncating label.
            Part of a MEASURED family, not a guess: a 390px census of all 55 surfaces found 21 with a
            clipped, unrecoverable label, and of 203 such elements 131 are identifiers like this one.
            The other 72 are deliberately NOT this fix — 67 are long prose where a title would be a
            wall of text, and 5 are overflowing containers rather than labels. */}
        <div data-type="caption" className="mt-0.5 truncate text-on-surface-low" title={label}>{label}</div>
      </div>
      {preferred && (
        <span
          data-type="caption" className="shrink-0 rounded px-1.5 py-0.5"
          style={accentChip}>
          higher-trust source
        </span>
      )}
    </div>
  )
}
