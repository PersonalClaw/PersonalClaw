import { useState } from 'react'
import { fvs } from '../../design/fontWeight'
import { Lightbulb, Loader2, Check, X, ChevronDown, ChevronRight, ShieldQuestion } from 'lucide-react'
import { api, type SkillProposal, type SkillProposalDetail, type SkillProposalFeed, type SkillLadderReview } from '../../lib/api'
import { Button } from '../../ui/Button'
import { ListSkeleton, EmptyState, LoadError } from '../../ui/ListScaffold'
import { UnifiedDiff } from '../../ui/UnifiedDiff'
import { useQuery, useMutation, invalidateKeys } from '../../lib/data'
import { TextLink } from '../../ui/TextLink'

/** Skill-proposals inbox (skill-evolution-proposal-only).
 *
 *  Autonomous skill synthesis PROPOSES, never installs — this is where a human
 *  reviews each proposal (its procedure + the fenced source trace that drove it)
 *  and accepts it into the live library or rejects it. Nothing here is running. */
export function SkillProposals() {
  // No `.catch(() => [])`. A swallowed rejection became an empty array, and "no skill proposals"
  // is a claim about the synthesizer — a user waiting on a proposal would read a failed read as
  // "it hasn't produced one yet" and stop looking.
  const { data: feed, error: loadErr, refresh } = useQuery<SkillProposalFeed>(
    'skill-proposals', () => api.skillProposals(),
  )
  const proposals = feed?.proposals
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
        hint={emptyHint(feed?.lastReview ?? null)}
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
        {' '}They also appear in your <TextLink href="#/inbox?kind=proposal">inbox</TextLink>.
      </p>
      {/* No `onChanged` prop. Each row's accept/reject DECLARES the keys it affects, and the layer
          re-reads them for every mounted reader — including this list and the SkillsPage badge that
          reads the same collection under `skill-proposals-count`. A callback threaded down to say
          "something changed, please refetch" is the manual refetch DSC-14 removes. */}
      {proposals.map((p) => <ProposalRow key={p.id} proposal={p} />)}
    </div>
  )
}

/** Verdicts that mean the pass WORKED (including deciding there was nothing to learn).
 *  Listed as the allowlist, not its complement, so an UNMAPPED verdict reads as
 *  "something to look at" — mirroring the backend, which logs an unrecognised verdict
 *  at WARNING deliberately, because a default branch that swallows the unknown into
 *  "all is well" is how this whole defect class reappears. */
const LADDER_HEALTHY = new Set(['env_failure_claim', 'no_action', 'enqueue_skipped', 'filed', 'template_filed', 'template_declined'])

/** What an empty queue MEANS, which it could not say before `lastReview` existed.
 *
 *  The old copy — "when the system synthesizes a skill it lands here" — was an
 *  unfalsifiable promise: identical whether the reviewer had run a hundred times and
 *  found nothing or had never run at all. Three states now, and the failure one is
 *  named as a failure rather than dressed as patience. */
export function emptyHint(last: SkillLadderReview | null): string {
  if (!last) {
    return 'The skill reviewer has not run yet. It runs after a substantial turn — one you corrected, or one that used several tools — and proposes here for review, never installing on its own.'
  }
  const when = new Date(last.at).toLocaleString()
  if (!LADDER_HEALTHY.has(last.verdict)) {
    return `The reviewer last ran ${when} but did not finish (${last.verdict}). That is a failure, not an idle queue — check the agent log and your background model provider.`
  }
  return `The reviewer ran ${when} and had nothing worth proposing. That is the healthy case: it only proposes when a session teaches it something durable, and it never installs on its own.`
}

/** What a stumble trigger reads as on the refine pill.
 *
 *  A MAP, not the raw value interpolated: an unknown trigger must render as nothing rather
 *  than leak an enum into the UI, which is the same reason the backend's overlay renderer maps
 *  it too. Short labels because this rides inside the existing pill beside "refine" — the
 *  proposal's own description carries the full sentence.
 *
 *  Exported for its unit test: it is a pure mapping and testing it through a render is testing
 *  React, not the mapping. */
export const TRIGGER_LABEL: Record<string, string> = {
  correction: 'you corrected it',
  failure_retry: 'a step was retried',
  rejection: 'you declined an action',
}

/** The refine pill's text — 'Refine', plus the reason when the trigger is one we know.
 *
 *  Capitalized, and no longer the bare `kind` value: `refine` was on `badgeCopyProse`'s
 *  exemption list precisely because it was a machine token with no prose form anywhere in the
 *  app. It has one now, so the exemption went with it. */
export function refinePillLabel(trigger?: string): string {
  const reason = TRIGGER_LABEL[trigger ?? '']
  return reason ? `Refine · ${reason}` : 'Refine'
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
    // The VERSION, not just the name. A refinement of a skill that already had refinements
    // reads identically to its first without it — and "which version did I approve?" is the
    // only question a refinement raises that the skill name cannot answer.
    onSuccess: (r) => setDone(r.version ? `Accepted → ${r.name} · refinement v${r.version}` : `Accepted → ${r.name}`),
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
            {proposal.kind === 'refine' && <span className="shrink-0 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.75rem]">{refinePillLabel(proposal.trigger)}</span>}
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
              {/* A refine proposal is a CHANGE, so it is shown as one: the unified diff of what
                  accepting does, computed by the backend from the skill's current body. The
                  diff's own `+` lines already carry the procedure text, so showing both would
                  be the same content twice. An empty diff on a refine is honest and named —
                  the target is gone, so accept would create a new skill instead. */}
              {detail.kind === 'refine' && detail.diff ? (
                <>
                  <div className="mb-1 text-on-surface-low text-[0.75rem] uppercase tracking-wide">
                    Change to {detail.refine_target}{detail.version ? ` — refinement v${detail.version}` : ''}
                  </div>
                  <UnifiedDiff
                    patch={detail.diff}
                    label={`Change to ${detail.refine_target || proposal.slug}`}
                    className="mb-3 overflow-x-auto rounded-md bg-surface px-3 py-2 font-mono text-[0.75rem] leading-snug"
                  />
                </>
              ) : (
                <>
                  {detail.kind === 'refine' && (
                    <p className="mb-2 text-on-surface-low text-[0.75rem]">
                      No diff: <span className="font-mono">{detail.refine_target || 'the target skill'}</span> is
                      no longer installed, so accepting this would add it as a new skill instead.
                    </p>
                  )}
                  <div className="mb-1 text-on-surface-low text-[0.75rem] uppercase tracking-wide">Procedure</div>
                  <pre className="mb-3 overflow-x-auto whitespace-pre-wrap rounded-md bg-surface px-3 py-2 text-on-surface text-[0.75rem]">{detail.procedure_md}</pre>
                </>
              )}
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
