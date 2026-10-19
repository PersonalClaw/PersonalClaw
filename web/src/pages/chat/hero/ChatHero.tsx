import { motion } from 'framer-motion'
import { Sparkles } from 'lucide-react'
import { AgentOrb } from '../../../ui/AgentOrb'
import { Skeleton, LoadingStatus } from '../../../ui/ListScaffold'
import { NeedsModelNote } from '../NoModelSetupState'
import { firstNameOf } from '../../../app/identity'
import { fadeEnter, spring } from '../../../design/motion'
import { api, type SessionTemplate } from '../../../lib/api'
import { refreshKinds, useChatSocket } from '../../../lib/useChatSocket'
import { useQuery, invalidateKeys } from '../../../lib/data'
import { ExplainCard } from './ExplainCard'

// ── The new chat's hero ────────────────────────────────────────────────────────────────────────
//
// What a new chat opens on: the agent orb and a greeting, the composer, and two strips beneath it —
// the owner's saved starters and suggestions drawn from memory and recent activity. Picking one
// fills the composer; nothing is sent until the owner sends it. The orb is its 64 px drawing, still
// and in the rest colour: nothing runs in a new chat, so nothing on it moves
// (`docs/design/agent-orb.md`, "Placement").

function greeting(name: string): string {
  const h = new Date().getHours()
  const part = h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening'
  return `Good ${part}, ${firstNameOf(name)}`
}

/** Contextual prompt-starter chips on the empty-chat hero. Sourced from the
 *  background-computed /api/suggestions (memory + recent activity), so they're
 *  personal, not generic. Clicking one fills the composer (the user reviews, then
 *  sends) rather than firing immediately. Silent when none are available. While no model is
 *  chosen for the chores the list is the standard one, and the line under it says why and links
 *  to where one is chosen (`needs_model`). */
function SuggestionChips({ onPick }: { onPick: (s: string) => void }) {
  // No `.catch`: this key is PERSISTED, so a swallowed rejection wrote a fabricated `[]` into
  // sessionStorage as though it were an answer, and the next visit painted "no suggestions" from
  // cache. Without it the rejection leaves `data` undefined and nothing is cached. The strip still
  // hides on failure, which is honest — a decoration that quietly does not appear claims nothing.
  const { data, loading } = useQuery('chat:suggestions', () => api.suggestions(), { persist: true })
  const items = (data?.suggestions ?? []).slice(0, 6)
  const needsModel = data?.needs_model ?? ''
  // The read answers at once with the list there is; a new one is written in the background and
  // lands as a `suggestions` refresh hint, so the strip re-reads then.
  useChatSocket((m) => { if (refreshKinds(m).includes('suggestions')) invalidateKeys('chat:suggestions') },
    () => invalidateKeys('chat:suggestions'))
  // 🪤 "STILL ASKING" IS NOT "NONE AVAILABLE", and this strip used to render `null` for both. The
  // docstring's "silent when none are available" is a deliberate product choice about the EMPTY
  // answer; it was never meant to cover the pending one. Collapsing the two is expensive here for a
  // reason specific to this hero: it is vertically CENTERED, so the strip arriving does not push
  // content down, it moves the mark, the greeting and the composer ALL of them, by half the strip's
  // height. `/api/suggestions` answers at once now, but a busy gateway can still answer late, and
  // nothing on screen would say a read is open.
  //
  // Measured on the e2e harness at 398e6b7a6: ONE `toHaveScreenshot` call on `#/chat` produced two
  // consecutive screenshots differing by 501,409 pixels — 54% of the image — so the route never
  // reached rest and yielded no verdict about its baseline at all. Gating on `loading` (the flag
  // `useQuery` documents for exactly this) puts a `.skeleton` on screen, which is what
  // `e2e/helpers.ts`'s `LOADING_SELECTOR` counts, so the settle barrier waits for the strip instead
  // of photographing the hero mid-flight. The settled render is unchanged, so no baseline moves.
  //
  // The placeholder mirrors the real strip's geometry — same flex container, same `maxWidth`, pill
  // heights matching the chips' `py-2` + `text-[0.8125rem]` box — so the swap is a text change
  // inside a stable layout rather than a second reflow of everything above it.
  if (loading && !data) {
    return (
      <div className="flex flex-wrap justify-center gap-2" style={{ maxWidth: 720 }} role="status" aria-busy="true">
        <LoadingStatus what="prompt suggestions" />
        {['w-[132px]', 'w-[104px]', 'w-[168px]', 'w-[148px]', 'w-[120px]', 'w-[156px]'].map((w) => (
          <Skeleton key={w} className={`h-[37px] ${w} rounded-pill`} />
        ))}
      </div>
    )
  }
  if (!items.length) return null
  return (
    <div className="flex flex-col items-center gap-s">
      <div className="flex flex-wrap justify-center gap-2" style={{ maxWidth: 720 }}>
        {items.map((s, i) => (
          <motion.button key={i} type="button" onClick={() => onPick(s)}
            variants={fadeEnter} initial="initial" animate="animate"
            className="rounded-pill border border-outline-variant/60 bg-surface-container px-3.5 py-2 text-left text-[0.8125rem] text-on-surface-var transition-colors hover:border-primary/40 hover:bg-surface-high hover:text-on-surface">
            {s}
          </motion.button>
        ))}
      </div>
      {needsModel && <NeedsModelNote sentence={needsModel} />}
    </div>
  )
}

/** Saved starters on the new-chat screen.
 *
 *  Picking one PREFILLS the composer selection (and the prompt, if the template has
 *  one) instead of creating a session server-side. A server-side
 *  `create_from_template() -> session_key` was considered, but this page mints a session lazily on
 *  first send — a second server-side creation path would mean two ways a session comes
 *  into existence, and an abandoned starter would leave an empty chat behind. Prefilling
 *  reuses the one `ensureSession` path, so a starter the user opens and walks away from
 *  costs nothing. */
function StarterChips({ onPick }: { onPick: (t: SessionTemplate) => void }) {
  // Same as the suggestion strip: persisted key, so the swallow cached a fabricated empty list.
  const { data, loading } = useQuery('chat:starters', () => api.sessionTemplates(), { persist: true })
  const items = (data ?? []).slice(0, 6)
  // Same pending-vs-empty split as the suggestion strip directly above, and it belongs here too even
  // though a fresh install answers `[]`: this strip sits ABOVE the suggestion strip in the same
  // centered hero, so a read that resolves late moves the same four elements. Gating on `loading`
  // keeps the two strips' waiting states consistent — the settle barrier sees ONE page that is still
  // reading rather than a page that is at rest between two arrivals. An install that genuinely has no
  // starters still renders nothing once the read lands, so the settled baseline is unchanged.
  if (loading && !data) {
    return (
      <div className="flex w-full flex-col items-center gap-2" role="status" aria-busy="true">
        <LoadingStatus what="your starters" />
        <Skeleton className="h-4 w-24" />
        <div className="flex flex-wrap justify-center gap-2" style={{ maxWidth: 720 }}>
          {['w-[146px]', 'w-[118px]', 'w-[162px]'].map((w) => (
            <Skeleton key={w} className={`h-[37px] ${w} rounded-pill`} />
          ))}
        </div>
      </div>
    )
  }
  if (!items.length) return null
  return (
    <div className="flex w-full flex-col items-center gap-2">
      <p className="text-[0.75rem] text-on-surface-low">Your starters</p>
      <div className="flex flex-wrap justify-center gap-2" style={{ maxWidth: 720 }}>
        {items.map((t) => (
          <motion.button key={t.id} type="button" onClick={() => onPick(t)}
            title={t.first_prompt || t.name}
            variants={fadeEnter} initial="initial" animate="animate"
            className="flex items-center gap-2 rounded-pill border border-primary/30 bg-primary-container/30 px-3.5 py-2 text-left text-[0.8125rem] text-on-surface-var transition-colors hover:border-primary/60 hover:bg-primary-container/50 hover:text-on-surface">
            <Sparkles size={13} className="shrink-0 text-primary" />
            {t.name}
          </motion.button>
        ))}
      </div>
    </div>
  )
}

export function ChatHero({ name, stage, onPickStarter, onPickSuggestion }: {
  name: string
  stage: React.ReactNode
  onPickStarter: (t: SessionTemplate) => void
  onPickSuggestion: (s: string) => void
}) {
  return (
    <div className="relative flex-1 flex flex-col items-center justify-center px-l">
      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring.spatialDefault}
        className="flex flex-col items-center gap-l mb-2xl">
        <AgentOrb phase="idle" size={64} />
        <h1 data-type="display-s" className="text-on-surface text-center">{greeting(name)}</h1>
      </motion.div>
      <div className="flex w-full flex-col items-center gap-2xl" style={{ maxWidth: 'var(--content-width)' }}>
        {stage}
        <ExplainCard />
        <StarterChips onPick={onPickStarter} />
        <SuggestionChips onPick={onPickSuggestion} />
      </div>
    </div>
  )
}
