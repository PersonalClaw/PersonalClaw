import { useState, type ReactNode } from 'react'
import { motion } from 'framer-motion'
import {
  MessageSquare, History, type LucideIcon,
  MessageSquarePlus, ListTodo, BookOpen, FolderKanban, FileCode2, TerminalSquare, Sparkles, Compass,
  Package, HardDrive,
} from 'lucide-react'
import { DashboardLiveProvider } from './DashboardLive'
import { PinnedTiles } from './PinnedTiles'
import { HeroPulse } from './widgets/HeroPulse'
import { ActionCenter } from './widgets/ActionCenter'
import { ActiveWork } from './widgets/ActiveWork'
import { TasksWidget } from './widgets/TasksWidget'
import { Suggestions } from './widgets/Suggestions'
import { Discover } from './widgets/Discover'
import { PinnedArtifacts } from './widgets/PinnedArtifacts'
import { OnThisMachine } from './widgets/OnThisMachine'
import { ScheduleWidget } from './widgets/ScheduleWidget'
import { SystemHealth } from './widgets/SystemHealth'
import { TopBar } from '../../ui/TopBar'
import { useIdentity, firstNameOf } from '../../app/identity'
import { api, type ChatSessionSummary } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { sessionRecencyMs } from '../../lib/epoch'
import { spring, expr } from '../../design/motion'
import { ComposerStage } from '../../ui/ComposerStage'
import { useComposerData } from '../../lib/useComposerData'
import type { ComposerValue } from '../../ui/composer/types'
import type { RouteProps } from '../../app/useQueryState'

/** The dashboard — PersonalClaw's home. Redesigned bare & launcher-forward: no
 *  bento boxes. A command launcher up top (jump straight into a chat or a
 *  feature), then boundary-less bands of live signal under hairline section
 *  labels — content owns the space, chrome doesn't. The customizable grid +
 *  per-user layout persistence were retired (clean break); everyone gets this
 *  one content-first layout. Widget bodies + the shared DashboardLive feed are
 *  reused verbatim; only their card chrome is gone. */
export function DashboardPage(route: RouteProps) {
  const { name } = useIdentity()
  return (
    <DashboardLiveProvider>
      <div className="flex h-full flex-col overflow-hidden">
        {/* The greeting is the page title — it lives in the header (no separate
            "Home" label needed), which reclaims the vertical space it used to
            take at the top of the launcher below. The Hero Pulse status strip
            (live counts + connectivity) sits right-aligned in the same header
            from `lg` up — full labels at 2xl+, minimized icon+count pills in
            between (HeroPulse degrades itself). Below `lg` even the minimized
            row would crush the greeting, so the header slot empties and the
            strip renders in the body under the launcher instead (same swap,
            other side, in the scroll area below). */}
        <TopBar
          contentAligned
          left={(
            <h1 data-type="headline-s" className="min-w-0 truncate text-on-surface">{greetingFor(name)}</h1>
          )}
          right={(
            <div className="hidden lg:block">
              <HeroPulse variant="header" {...route} />
            </div>
          )}
        />
        <div className="min-h-0 flex-1 overflow-y-auto">
          {/* Honor the global content-width preference (Full / narrower presets)
              set from the shell corner — the same --content-width every other
              centered page column tracks. */}
          <div className="mx-auto flex w-full flex-col gap-2xl px-l py-xl" style={{ maxWidth: 'var(--content-width)' }}>
            <Launcher {...route} />

            {/* Live signal strip — the header's Hero Pulse, relocated below the
                launcher when even the minimized header row won't fit (< lg). */}
            <div className="min-h-9 lg:hidden"><HeroPulse {...route} /></div>

            {/* Pinned tiles band (AMBIENT-SURFACES §1) — the ADDITIVE half of the
                dashboard-as-views registry. Renders the Overview view's artifact
                tiles only; an empty registry renders NOTHING, so a fresh install is
                byte-identical to today's fixed layout below. */}
            <PinnedTiles />

            {/* Prime signal: what needs you + what's running, side by side on wide
                screens, stacked on narrow. Bare sections, hairline-labelled. */}
            <div className="grid grid-cols-1 gap-2xl lg:grid-cols-2">
              <Section label="Needs you" icon={ListTodo} tour="approvals">
                <ActionCenter {...route} />
              </Section>
              <Section label="Active work" icon={Sparkles}>
                <ActiveWork {...route} />
              </Section>
            </div>

            <div className="grid grid-cols-1 gap-2xl lg:grid-cols-2">
              <Section label="Tasks" icon={ListTodo}>
                <TasksWidget {...route} />
              </Section>
              <Section label="Suggestions" icon={Sparkles}>
                <Suggestions {...route} />
              </Section>
            </div>

            {/* Discover (§6) — a curated spotlight of the parts of PersonalClaw you
                haven't tried yet, each a deep link. The full grouped list is the
                dedicated Discover hub (the widget's "See all" jumps there). */}
            <Section label="Discover" icon={Compass}>
              <Discover {...route} />
            </Section>

            {/* Pinned artifacts (WORK-CONTAINERS §6.5d) — hard-imported, the established
                widget pattern. There is no tile registry to register with: the bento grid and
                per-user layout persistence were deliberately retired, so a pin is a slug in a
                list that THIS component renders. */}
            <Section label="Pinned artifacts" icon={Package}>
              <PinnedArtifacts {...route} />
            </Section>

            {/* On this machine (LOCAL-MODEL-MANAGER-V2 §7) — which local models are
                holding RAM right now, and the machine's memory pressure. Same shape as
                Pinned artifacts: a hard-imported widget in a Section band, because the
                bento tile registry was retired. */}
            <Section label="On this machine" icon={HardDrive}>
              <OnThisMachine />
            </Section>

            <Section label="Recent activity" icon={History}>
              <ScheduleWidget {...route} />
            </Section>
          </div>
        </div>
        {/* System rail — docked to the dashboard's bottom edge, OUTSIDE the scroll
            area above. A flex sibling with `shrink-0` pins it to the bottom while
            the scroll area owns `flex-1`, so the live system indicators stay
            visible however far the content above scrolls. The outer band is
            transparent and just provides the gutter; the rail itself is a floating,
            rounded, frosted ISLAND — the same shell-chrome language as the composer
            and ShellCorners (token border + rest-shadow + backdrop blur) — instead
            of a flat full-bleed strip, so it reads as a sleek docked control rather
            than a boxy footer. */}
        <div className="shrink-0 px-l pb-m pt-xs">
          {/* `@container` makes the island the query context for the rail: its
              width tracks --content-width (NOT the viewport), so the rail members
              adapt to the space actually available (SystemHealth's `@…` variants),
              staying on one line as long as they fit. */}
          <div
            className="@container mx-auto flex w-full items-center rounded-lg border border-outline-variant/50 bg-surface-low/70 px-l py-s shadow-rest backdrop-blur-md"
            style={{ maxWidth: 'var(--content-width)' }}
          >
            <SystemHealth {...route} />
          </div>
        </div>
      </div>
    </DashboardLiveProvider>
  )
}

/** Time-of-day greeting, personalised when we know the user's name. Rendered as
 *  the dashboard's page title in the header. */
function greetingFor(name: string | undefined): string {
  const h = new Date().getHours()
  const part = h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening'
  return name ? `Good ${part}, ${firstNameOf(name)}` : `Good ${part}`
}

// ── Launcher hero ───────────────────────────────────────────────────────────
// The front door: an Ask composer that launches a fresh chat seeded with the
// typed text, a row of quick feature jumps, and recent-chat chips. (The greeting
// moved to the page header.)

const JUMPS: { label: string; icon: LucideIcon; go: string }[] = [
  { label: 'Chat', icon: MessageSquarePlus, go: 'chat/new' },
  { label: 'Tasks', icon: ListTodo, go: 'tasks' },
  { label: 'Knowledge', icon: BookOpen, go: 'knowledge' },
  { label: 'Projects', icon: FolderKanban, go: 'projects' },
  { label: 'Files', icon: FileCode2, go: 'files' },
  { label: 'Terminal', icon: TerminalSquare, go: 'terminal' },
]

/** Dashboard launcher controls — enables the same agent/model/attach/mic/optimize
 *  affordances the new-chat composer has, so users don't wonder why the dashboard
 *  input is limited compared to the chat page. */
const LAUNCHER_CONTROLS = { agent: true, model: true, approval: false, reasoning: true, attach: true, mic: true, optimize: true, slash: true }

function Launcher({ navigate }: RouteProps) {
  const [text, setText] = useState('')
  const data = useComposerData()
  const [selection, setSelection] = useState<ComposerValue>({ agent: '', model: 'Auto', approval: 'normal', taskMode: 'agent', reasoning: '' })
  // `.catch(() => [])` was worse here than elsewhere because this key is PERSISTED: the
  // fabricated empty array was written to sessionStorage as if it were an answer, so a failed
  // load poisoned the cache and the next visit painted "no recent chats" instantly from it.
  // Without the catch, a rejection leaves `data` undefined and nothing is cached.
  //
  // The chip row stays hidden on failure rather than growing an error of its own: it is a
  // shortcut, not the record, and the dashboard has no slot-level error idiom — inventing one
  // for a single adopter would be speculative API. The authoritative surface (chat history)
  // announces the failure, which is where a user goes to look for their chats.
  const { data: sessions } = useCachedData<ChatSessionSummary[]>(
    'dashboard:recent-sessions', () => api.chatSessions(), { persist: true },
  )

  const launch = () => {
    const t = text.trim()
    if (!t) { navigate('chat/new'); return }
    // Seed includes selected agent/model so the new chat opens already bound.
    const params = new URLSearchParams({ seed: t })
    if (selection.agent) params.set('agent', selection.agent)
    if (selection.model && selection.model !== 'Auto') params.set('model', selection.model)
    navigate(`chat/new?${params}`)
    setText('')
  }

  const recent = (sessions ?? [])
    .filter((s) => s.title && !s.running)
    .sort((a, b) => sessionRecencyMs(b) - sessionRecencyMs(a))
    .slice(0, 4)

  return (
    <div className="flex flex-col gap-l">
      {/* The real Composer component — same agent/model picker, file attach, mic,
          optimize, and slash commands the chat page has. On send it seeds a new chat
          with the typed text + chosen agent/model instead of an in-place turn. */}
      <ComposerStage value={text} onChange={setText} onSend={launch}
        placeholder="Ask anything, or start a task…"
        controls={LAUNCHER_CONTROLS} data={data}
        selection={selection} onSelect={(patch) => setSelection((s) => ({ ...s, ...patch }))}
        onAttach={() => {
          // Attach goes through the seeded chat: open chat/new and let it handle the files.
          const t = text.trim()
          navigate(t ? `chat/new?seed=${encodeURIComponent(t)}` : 'chat/new')
        }}
      />

      {/* Quick feature jumps + recent chats — flow inline, no boxes. */}
      <div className="flex flex-wrap items-center gap-xs">
        {JUMPS.map((j, i) => (
          <motion.button
            key={j.go}
            type="button"
            onClick={() => navigate(j.go)}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0, transition: { ...spring.spatialDefault, delay: Math.min(i * 0.03, 0.2) } }}
            whileHover={{ y: -expr(2, 0.3) }}
            className="inline-flex items-center gap-xs rounded-pill bg-surface-low px-m py-s text-on-surface-var transition-colors hover:bg-surface-high hover:text-on-surface"
            data-type="label-m"
          >
            <j.icon size={15} className="text-primary" /> {j.label}
          </motion.button>
        ))}
      </div>

      {recent.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-l gap-y-xs">
          <span data-type="label-m" className="flex items-center gap-xs text-on-surface-low"><History size={12} /> Jump back in</span>
          {recent.map((s) => (
            <button
              key={s.key}
              type="button"
              onClick={() => navigate(`chat/${encodeURIComponent(s.key)}`)}
              // 20px tall with 16px between chips: under SC 2.5.8's 24px minimum, and the spacing
              // exception needs 24px of clearance. `min-h-6` + `-my-0.5` grows the target and hands
              // the 4px back, so the row does not reflow — the Toggle fix's shape.
              className="inline-flex min-h-6 -my-0.5 items-center gap-xs text-on-surface-var transition-colors hover:text-on-surface"
              data-type="body-m"
            >
              <MessageSquare size={12} className="shrink-0 text-on-surface-low" /> <span className="max-w-[16rem] truncate">{s.title}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Bare section header ───────────────────────────────────────────────────────
// A section is a label + hairline, NOT a bordered card. Content flows directly
// beneath, owning the horizontal space.
function Section({ label, icon: Icon, children, tour }: {
  label: string; icon: LucideIcon; children: ReactNode
  /** Product-tour anchor for this band (ONBOARDING-UX T5.1) — set on the band rather than
   *  on the widget inside it, so a band whose widget swaps to its empty state still anchors. */
  tour?: string
}) {
  return (
    <section data-tour={tour} className="flex min-w-0 flex-col gap-s">
      <div className="flex items-center gap-s">
        <Icon size={14} className="shrink-0 text-on-surface-low" />
        {/* h2, not h3: this is a top-level section directly under the page's h1 greeting, and
            every one of the dashboard's sections renders through here. As h3 the page read
            h1 -> h3 seven times over, skipping a level for a screen-reader user navigating by
            heading. `data-type="label-l"` carries the size and weight, so the level is purely
            structural — the heading looks identical. */}
        <h2 data-type="label-l" className="text-on-surface-var">{label}</h2>
        <span className="h-px flex-1 bg-outline-variant/40" />
      </div>
      {children}
    </section>
  )
}

