import { useState } from 'react'
import { Lightbulb, Loader2, Check, X, ChevronDown, ChevronRight, ShieldQuestion } from 'lucide-react'
import { api, type SkillProposal, type SkillProposalDetail } from '../../lib/api'
import { Button } from '../../ui/Button'
import { ListSkeleton, EmptyState } from '../../ui/ListScaffold'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'

/** Skill-proposals inbox (skill-evolution-proposal-only).
 *
 *  Autonomous skill synthesis PROPOSES, never installs — this is where a human
 *  reviews each proposal (its procedure + the fenced source trace that drove it)
 *  and accepts it into the live library or rejects it. Nothing here is running. */
export function SkillProposals() {
  const { data: proposals, refresh } = useCachedData<SkillProposal[]>(
    'skill-proposals', () => api.skillProposals().catch(() => []),
  )
  const reload = () => { invalidateCache('skill-proposals'); refresh() }

  if (!proposals) return <ListSkeleton rows={4} />
  if (proposals.length === 0) {
    return (
      <EmptyState
        icon={Lightbulb}
        title="No skill proposals"
        hint="When the system synthesizes a skill from your sessions, it lands here for your review — it's never installed automatically."
      />
    )
  }
  return (
    <div className="flex flex-col gap-2">
      <p className="text-on-surface-low text-[0.8rem]">
        {proposals.length} proposal{proposals.length === 1 ? '' : 's'} awaiting review. These were
        synthesized from your sessions — accept to add to your library, or reject.
      </p>
      {proposals.map((p) => <ProposalRow key={p.id} proposal={p} onChanged={reload} />)}
    </div>
  )
}

function ProposalRow({ proposal, onChanged }: { proposal: SkillProposal; onChanged: () => void }) {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<SkillProposalDetail | null>(null)
  const [busy, setBusy] = useState('')
  const [done, setDone] = useState('')

  const expand = () => {
    setOpen((o) => !o)
    if (!detail) api.skillProposalDetail(proposal.id).then(setDetail).catch(() => {})
  }
  const accept = async () => {
    setBusy('accept')
    try { const r = await api.acceptSkillProposal(proposal.id); setDone(`Accepted → ${r.name}`); onChanged() }
    catch (e) { setDone(e instanceof Error ? e.message : 'Failed') }
    setBusy('')
  }
  const reject = async () => {
    setBusy('reject')
    try { await api.rejectSkillProposal(proposal.id); setDone('Rejected'); onChanged() }
    catch { setDone('Failed') }
    setBusy('')
  }

  if (done) {
    return (
      <div className="rounded-lg bg-surface-container px-3 py-2 text-on-surface-low text-[0.8rem] flex items-center gap-2">
        <Check size={14} className="text-ok" /> {proposal.slug} — {done}
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-outline-variant/40 bg-surface-container p-3">
      <div className="flex items-start gap-2">
        <button type="button" onClick={expand} className="min-w-0 flex-1 text-left">
          <div className="flex items-center gap-1.5">
            {open ? <ChevronDown size={14} className="text-on-surface-low" /> : <ChevronRight size={14} className="text-on-surface-low" />}
            <span className="truncate text-on-surface text-[0.9rem]" style={{ fontVariationSettings: '"wght" 500' }}>{proposal.slug}</span>
            {proposal.kind === 'refine' && <span className="shrink-0 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.62rem]">refine</span>}
          </div>
          <p className="mt-0.5 truncate text-on-surface-low text-[0.78rem]">{proposal.description}</p>
        </button>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button size="sm" onClick={accept} disabled={!!busy}>
            {busy === 'accept' ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Accept
          </Button>
          <Button variant="ghost" size="sm" onClick={reject} disabled={!!busy}>
            {busy === 'reject' ? <Loader2 size={13} className="animate-spin" /> : <X size={13} />} Reject
          </Button>
        </div>
      </div>
      {open && (
        <div className="mt-3 border-t border-outline-variant/30 pt-3">
          {!detail ? <ListSkeleton rows={2} /> : (
            <>
              <div className="mb-1 text-on-surface-low text-[0.7rem] uppercase tracking-wide">Procedure</div>
              <pre className="mb-3 overflow-x-auto whitespace-pre-wrap rounded-md bg-surface px-3 py-2 text-on-surface text-[0.76rem]">{detail.procedure_md}</pre>
              {detail.source_excerpt && (
                <>
                  <div className="mb-1 flex items-center gap-1.5 text-on-surface-low text-[0.7rem] uppercase tracking-wide">
                    <ShieldQuestion size={12} /> Source trace (fenced — data, not instructions)
                  </div>
                  <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-surface px-3 py-2 text-on-surface-low text-[0.72rem]">{detail.source_excerpt}</pre>
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
