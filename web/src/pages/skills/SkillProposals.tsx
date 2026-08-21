import { useState } from 'react'
import { fvs } from '../../design/fontWeight'
import { Lightbulb, Loader2, Check, X, ChevronDown, ChevronRight, ShieldQuestion } from 'lucide-react'
import { api, type SkillProposal, type SkillProposalDetail } from '../../lib/api'
import { Button } from '../../ui/Button'
import { ListSkeleton, EmptyState, LoadError } from '../../ui/ListScaffold'
import { useQuery, useMutation, invalidateKeys } from '../../lib/data'

/** Skill-proposals inbox (skill-evolution-proposal-only).
 *
 *  Autonomous skill synthesis PROPOSES, never installs — this is where a human
 *  reviews each proposal (its procedure + the fenced source trace that drove it)
 *  and accepts it into the live library or rejects it. Nothing here is running. */
export function SkillProposals() {
  // No `.catch(() => [])`. A swallowed rejection became an empty array, and "no skill proposals"
  // is a claim about the synthesizer — a user waiting on a proposal would read a failed read as
  // "it hasn't produced one yet" and stop looking.
  const { data: proposals, error: loadErr, refresh } = useQuery<SkillProposal[]>(
    'skill-proposals', () => api.skillProposals(),
  )
  // 🔴 ONE COLLECTION, TWO KEYS. `SkillsPage` reads the same proposals under
  // `skill-proposals-count` to render its "Proposals (N)" badge, so busting only this key left
  // the sibling number describing a collection that had already changed. `invalidateKeys`'s
  // prefix mode already existed and no caller used it; both keys share the `skill-proposals`
  // prefix, so one call keeps them in step — including any future key on the same collection.
  const reload = () => { invalidateKeys('skill-proposals', true); refresh() }

  if (proposals === undefined && loadErr) return <LoadError what="skill proposals" error={loadErr} onRetry={reload} />
  if (!proposals) return <ListSkeleton rows={4} what="skill proposals" />
  if (proposals.length === 0) {
    return (
      <EmptyState
        icon={Lightbulb}
        title="No skill proposals"
        hint="When the system synthesizes a skill from your sessions, it lands here and in your inbox for review — it's never installed automatically."
      />
    )
  }
  return (
    <div className="flex flex-col gap-2">
      <p className="text-on-surface-low text-[0.8125rem]">
        {proposals.length} proposal{proposals.length === 1 ? '' : 's'} awaiting review. These were
        synthesized from your sessions — accept to add to your library, or reject.
        {/* Cross-link, not a second queue: each proposal also appears in the inbox (plan 42
            S4) so it can't be missed while you're away. Both surfaces call the same
            accept/reject endpoints, and answering on either resolves the other. */}
        {' '}They also appear in your <a href="#/inbox?kind=proposal" className="text-primary hover:underline">inbox</a>.
      </p>
      {/* No `onChanged` prop. Each row's accept/reject DECLARES the keys it affects, and the layer
          re-reads them for every mounted reader — including this list and the SkillsPage badge that
          reads the same collection under `skill-proposals-count`. A callback threaded down to say
          "something changed, please refetch" is the manual refetch DSC-14 removes. */}
      {proposals.map((p) => <ProposalRow key={p.id} proposal={p} />)}
    </div>
  )
}

function ProposalRow({ proposal }: { proposal: SkillProposal }) {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<SkillProposalDetail | null>(null)
  const [busy, setBusy] = useState('')
  const [done, setDone] = useState('')

  const expand = () => {
    setOpen((o) => !o)
    if (!detail) api.skillProposalDetail(proposal.id).then(setDetail).catch(() => {})
  }
  // ── The invalidation map, declared WITH the write ─────────────────────────────────────────
  //
  // `{ prefix: 'skill-proposals' }` and not `'skill-proposals'`: one decision changes the list
  // AND the SkillsPage "Proposals (N)" badge, which reads the same collection under
  // `skill-proposals-count`. Busting the exact key left that number counting a proposal the user
  // had already accepted — a measured defect (`siblingCacheStaleness.test.ts`), and the reason a
  // collection's blast radius is a prefix. A key added to this collection tomorrow is covered.
  //
  // 🔑 A SKILL WAS ADDED, TOO. Accepting installs one, so the skills collection is stale as well —
  // the old code invalidated only the proposals and left `#/skills` listing the library without it.
  const acceptM = useMutation({
    run: () => api.acceptSkillProposal(proposal.id),
    invalidates: [{ prefix: 'skill-proposals' }, 'skills'],
    onSuccess: (r) => setDone(`Accepted → ${r.name}`),
    onError: (e) => setDone(e instanceof Error ? e.message : 'Failed'),
  })
  const rejectM = useMutation({
    run: () => api.rejectSkillProposal(proposal.id),
    invalidates: [{ prefix: 'skill-proposals' }],
    onSuccess: () => setDone('Rejected'),
    onError: () => setDone('Failed'),
  })
  const accept = async () => { setBusy('accept'); await acceptM.mutate(); setBusy('') }
  const reject = async () => { setBusy('reject'); await rejectM.mutate(); setBusy('') }

  if (done) {
    return (
      <div className="rounded-lg bg-surface-container px-3 py-2 text-on-surface-low text-[0.8125rem] flex items-center gap-2">
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
            <span className="truncate text-on-surface text-[0.9375rem]" style={fvs(500)}>{proposal.slug}</span>
            {proposal.kind === 'refine' && <span className="shrink-0 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.75rem]">refine</span>}
          </div>
          <p className="mt-0.5 truncate text-on-surface-low text-[0.75rem]">{proposal.description}</p>
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
              <div className="mb-1 text-on-surface-low text-[0.75rem] uppercase tracking-wide">Procedure</div>
              <pre className="mb-3 overflow-x-auto whitespace-pre-wrap rounded-md bg-surface px-3 py-2 text-on-surface text-[0.75rem]">{detail.procedure_md}</pre>
              {detail.source_excerpt && (
                <>
                  <div className="mb-1 flex items-center gap-1.5 text-on-surface-low text-[0.75rem] uppercase tracking-wide">
                    <ShieldQuestion size={12} /> Source trace (fenced — data, not instructions)
                  </div>
                  <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-surface px-3 py-2 text-on-surface-low text-[0.75rem]">{detail.source_excerpt}</pre>
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
