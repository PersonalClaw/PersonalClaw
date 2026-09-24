import { Sparkles, RefreshCw, Lightbulb, Brain } from 'lucide-react'
import { Surface } from '../../ui/Surface'
import { useQuery } from '../../lib/data'
import { api, type LearningSummary, type LearningSummaryGroup } from '../../lib/api'
import { fvs } from '../../design/fontWeight'

/** One row of the block: icon, exact count, and the names behind it.
 *
 *  The count comes from `group.count`, NEVER from `group.names.length` — the backend caps
 *  the sample at 8 while keeping the count exact, so deriving the count from the list it
 *  truncated would under-report the moment a group got busy. When the sample is short of
 *  the count the remainder is stated ("+3 more") rather than dropped silently, which is
 *  the only way "2 of 5 names" reads as a sample instead of as the whole truth.
 *
 *  Non-interactive on purpose: `title` carries the untruncated name list as a hover
 *  affordance, and the visible label carries the count AND the names, because a `title`
 *  on a non-interactive element is not an accessible name (the same call LV-2's chips
 *  made). There is no per-name surface to land on from here. */
function SummaryRow({ icon, label, group }: { icon: React.ReactNode; label: string; group: LearningSummaryGroup }) {
  const more = group.count - group.names.length
  return (
    <div className="flex items-start gap-s text-[0.8125rem] leading-snug" title={group.names.join('\n')}>
      <span className="mt-px shrink-0 opacity-70">{icon}</span>
      <span className="shrink-0 text-on-surface-var" style={fvs(600)}>{group.count} {label}</span>
      {group.names.length > 0 && (
        <span className="min-w-0 flex-1 truncate text-on-surface-low">
          {group.names.join(', ')}{more > 0 && ` +${more} more`}
        </span>
      )}
    </div>
  )
}

/** The learning summary block (LEARNING-VISIBILITY T2.3 / LV-3) — "what did this thing
 *  learn lately", as new/refined/pending counts with the names behind them.
 *
 *  **This is the FALLBACK surface, and deliberately so.** T2.3 asked for the block to be
 *  registered with plan 42's digest builder; that builder does not exist in the tree (no
 *  digest-section registry of any name), and the task row plus the atom's `done_when` both
 *  sanction rendering the same block on the skills page header instead. The gather itself
 *  lives in ONE place server-side (`learning_summary.compose_learning_summary`), so a
 *  digest builder consumes it rather than growing a second implementation.
 *
 *  Absent, never zeroed. Two cases collapse to "render nothing":
 *  - `learning.enabled` is off, so the route 404s. Rendering "0 new, 0 refined" there
 *    would claim nothing was learned when the truthful answer is "not being tracked".
 *  - nothing was learned inside the window. A block asserting four zeros is noise on
 *    every fresh install, and the page's own empty state already says the useful thing.
 *
 *  The read is `.catch(() => null)` for the reason SkillsPage's proposal-count read is:
 *  this is a supplementary summary above the list, not the list itself. The installed-skills
 *  read keeps its hard error surface (`LoadError`) because an empty list there IS a lie. */
export function LearningSummaryBlock() {
  // Key named after the COLLECTION it reads (`learning:`, already a declared namespace),
  // not after the skills page that renders it — a `skills:`-prefixed key would be missed by
  // `invalidateKeys('learning:', true)` when a proposal is accepted elsewhere.
  const { data } = useQuery<LearningSummary | null>('learning:summary', () => api.learningSummary().catch(() => null))
  if (!data || data.total <= 0) return null
  return (
    <Surface tone="low" radius="lg" className="mb-l px-m py-s">
      {/* `role="region"` is written out rather than left implicit. A named `<section>` already
          maps to `region` in HTML-AAM, but `design/ariaProhibitedAttr.test.ts` treats a
          `<section aria-label>` with no explicit role as a discarded name — and the ratchet is
          right to, because the mapping is name-conditional and one refactor that drops the label
          silently turns the element generic. Stating the role makes it unconditional. */}
      <section role="region" aria-label={`Learned in the last ${data.window_days} days`} className="flex flex-col gap-1.5">
        <div className="text-[0.75rem] uppercase tracking-wide text-on-surface-low/80" style={fvs(600)}>
          Learned in the last {data.window_days} days
        </div>
        {data.new_skills.count > 0 && (
          <SummaryRow icon={<Sparkles size={13} />} label="new" group={data.new_skills} />
        )}
        {data.refined_skills.count > 0 && (
          <SummaryRow icon={<RefreshCw size={13} />} label="refined" group={data.refined_skills} />
        )}
        {data.pending_proposals.count > 0 && (
          <SummaryRow icon={<Lightbulb size={13} />} label="pending" group={data.pending_proposals} />
        )}
        {data.facts.count > 0 && (
          <SummaryRow icon={<Brain size={13} />} label="facts" group={data.facts} />
        )}
      </section>
    </Surface>
  )
}
