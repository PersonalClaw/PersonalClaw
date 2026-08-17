import { useEffect, useState } from 'react'
import { AlertTriangle, Scale, Sparkles } from 'lucide-react'
import { api, type KnowledgeConflict } from '../../lib/api'
import { EmptyState, ListSkeleton } from '../../ui/ListScaffold'
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
 *  `basis` is rendered distinctly because a deterministic finding and a fast model's opinion
 *  deserve different trust, and the claim text alone does not say which one you are looking
 *  at. `prefer` shows the source-precedence ladder's advice, and shows nothing when the ladder
 *  cannot decide — two same-tier sources genuinely have no winner, and inventing one would
 *  manufacture authority out of arrival order. */
export function ConflictPanel() {
  const [conflicts, setConflicts] = useState<KnowledgeConflict[] | null>(null)

  useEffect(() => {
    let alive = true
    api.knowledgeConflicts()
      .then((d) => { if (alive) setConflicts(d.conflicts) })
      .catch(() => { if (alive) setConflicts([]) })
    return () => { alive = false }
  }, [])

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
  const proven = conflict.basis === 'deterministic'
  return (
    <div className="rounded-lg border border-outline-variant bg-surface p-3 text-[0.8125rem]">
      <div className="mb-2 flex items-center gap-2 text-[0.75rem] text-on-surface-low">
        {proven
          ? <AlertTriangle size={13} className="text-warning" aria-hidden />
          : <Sparkles size={13} aria-hidden />}
        <span style={fvs(600)}>{proven ? 'Provable conflict' : 'Possible conflict'}</span>
        <span aria-hidden>·</span>
        <span>{conflict.kind}</span>
        {!proven && (
          <>
            <span aria-hidden>·</span>
            {/* Stated for the model tier only: a proof has no meaningful confidence to show,
                and printing "100%" next to it would imply the two tiers are the same kind of
                claim measured on one scale. */}
            <span>{Math.round(conflict.confidence * 100)}% confident</span>
          </>
        )}
      </div>

      <ClaimSide
        text={conflict.left_claim}
        preferred={conflict.prefer === 'left'}
        label={conflict.item_title || conflict.left_item}
      />
      <div className="my-1 pl-3 text-[0.75rem] text-on-surface-low">versus</div>
      <ClaimSide
        text={conflict.right_claim}
        preferred={conflict.prefer === 'right'}
        label={conflict.right_item}
      />

      {conflict.detail && (
        <div className="mt-2 text-[0.75rem] text-on-surface-low">{conflict.detail}</div>
      )}
      {conflict.prefer === '' && (
        <div className="mt-2 text-[0.75rem] text-on-surface-low">
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
        <div className="mt-0.5 truncate text-[0.75rem] text-on-surface-low">{label}</div>
      </div>
      {preferred && (
        <span
          className="shrink-0 rounded px-1.5 py-0.5 text-[0.75rem]"
          style={accentChip}>
          higher-trust source
        </span>
      )}
    </div>
  )
}
