import { useCallback, useState } from 'react'
import { motion } from 'framer-motion'
import { BookOpen, BellRing, Repeat, Check, ArrowRight, ExternalLink } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Button } from '../../ui/Button'
import { InlineError } from '../../ui/InlineError'
import { TextLink } from '../../ui/TextLink'
import { listItemEnter, stagger, spring } from '../../design/motion'
import { fvs } from '../../design/fontWeight'
import type { OnboardingStatePatch } from '../../lib/api'
import {
  TRY_ONE_FLOWS, failureText, settingsTargetFor,
  type SettingsTarget, type TryOneId, type TryOneOutcome,
} from './tryOneFlows'

/** ONBOARDING-UX S1 T1.3 (OU-3) — the first-success step: three cards that DO the
 *  thing, on a real install, and show what happened.
 *
 *  **The line this step is built on: a card that opens a pre-filled form is not a
 *  first success.** Every button here runs `tryOneFlows`' real endpoint chain and
 *  then renders facts read back out of the real responses — the passage the
 *  retrieval actually returned, the notification text that actually landed, the
 *  status the loop store actually holds. Nothing on a settled card is echoed from
 *  the request that was sent.
 *
 *  **The failure branch is not a footnote.** The state this step must survive is the
 *  one the plan's Risks section names: the essentials step's provider Test PASSED,
 *  and the first real call is refused anyway. When that happens a card shows the
 *  gateway's own sentence verbatim and offers a deep-link into the Settings surface
 *  that owns the failure — which, because the route guard holds a non-onboarded user
 *  on `#/onboarding`, has to hand the destination to the guard rather than navigate
 *  against it (see `exitTo.ts`).
 *
 *  **Skipping is free.** None of the three is required to continue, and none is
 *  retried automatically. A first run that cannot be walked past is a trap.
 *
 *  Not built on `ui/PresetEmptyState`'s `PresetCard`: that primitive is deliberately
 *  ONE tab stop whose whole body is a `TileButton` with no interactive children,
 *  because a button inside a button is `nested-interactive`. These cards grow
 *  controls after they run — a run button, then an outcome link, then possibly a
 *  Settings deep-link and a retry — so they cannot be a single click target. The
 *  chrome is composed from the kit instead (`Button`, `TextLink`, `InlineError`),
 *  which is the part that would otherwise drift. */

interface CardDef {
  id: TryOneId
  icon: LucideIcon
  title: string
  /** What the button will actually do, in one line. Promises only what the flow does. */
  blurb: string
  /** The verb on the button. */
  action: string
}

const CARDS: CardDef[] = [
  {
    id: 'knowledge', icon: BookOpen, title: 'Teach it something',
    blurb: 'Saves a real note to Knowledge, then asks your library a question and shows the passage that answered it.',
    action: 'Save and ask',
  },
  {
    id: 'trigger', icon: BellRing, title: 'Set a reminder',
    blurb: 'Creates a real 9:00 AM reminder and fires it once now, so you see exactly what it will say.',
    action: 'Create and fire once',
  },
  {
    id: 'loop', icon: Repeat, title: 'Start a loop',
    blurb: 'Creates a one-cycle goal loop and starts it for real. It stops on its own.',
    action: 'Start it',
  },
]

type CardState =
  | { phase: 'idle' }
  | { phase: 'running' }
  | { phase: 'done'; outcome: TryOneOutcome }
  | { phase: 'failed'; message: string; target: SettingsTarget }

export function TryOneStep({ onProgress, onDone, onSkip, onExitTo }: {
  /** Persist a partial patch of first-run progress (OU-1's both-level merge). */
  onProgress: (patch: OnboardingStatePatch) => void
  /** At least one card succeeded and the user is moving on. */
  onDone: (summary: string) => void
  /** Move on having tried nothing — always available. */
  onSkip: () => void
  /** Leave the flow entirely and land on `path` (a hash path, no leading `#/`). */
  onExitTo: (path: string) => void
}) {
  const [states, setStates] = useState<Record<string, CardState>>({})
  const stateOf = (id: TryOneId): CardState => states[id] ?? { phase: 'idle' }

  const run = useCallback(async (id: TryOneId) => {
    setStates((m) => ({ ...m, [id]: { phase: 'running' } }))
    try {
      const outcome = await TRY_ONE_FLOWS[id]()
      setStates((m) => ({ ...m, [id]: { phase: 'done', outcome } }))
      // Each card records ONLY its own flag — the backend merges at both levels, so a
      // card never has to read back and echo its siblings to avoid clobbering them.
      onProgress({ first_success: { [id]: true } })
    } catch (e) {
      const message = failureText(e)
      const status = (e as { status?: number } | null)?.status
      setStates((m) => ({ ...m, [id]: { phase: 'failed', message, target: settingsTargetFor(message, status) } }))
    }
  }, [onProgress])

  const doneCount = CARDS.filter((c) => stateOf(c.id).phase === 'done').length

  return (
    <div className="flex flex-col gap-l">
      <p className="text-on-surface-var text-[0.8125rem]">
        Each of these runs for real on this machine — nothing is a preview. Try one, try all
        three, or skip straight to the dashboard.
      </p>

      <motion.div className="flex flex-col gap-s" initial="initial" animate="animate"
        variants={{ animate: { transition: stagger(0.05) } }}>
        {CARDS.map((c) => (
          <TryOneCard key={c.id} def={c} state={stateOf(c.id)}
            onRun={() => run(c.id)} onExitTo={onExitTo} />
        ))}
      </motion.div>

      <div className="flex items-center gap-m">
        <Button variant="primary" size="md"
          onClick={() => onDone(doneCount ? `${doneCount} of 3 tried` : 'Skipped')}>
          Continue <ArrowRight size={16} aria-hidden="true" />
        </Button>
        {/* Never a wall: the step is an offer, and OU-4's full-skip path runs through here. */}
        {doneCount === 0 && <TextLink onClick={onSkip}>Skip this</TextLink>}
      </div>
    </div>
  )
}

function TryOneCard({ def, state, onRun, onExitTo }: {
  def: CardDef; state: CardState
  onRun: () => void
  onExitTo: (path: string) => void
}) {
  const { icon: Icon, title, blurb, action } = def
  const done = state.phase === 'done'
  return (
    <motion.div variants={listItemEnter} layout transition={spring.spatialFast}
      className="rounded-lg bg-surface-high p-3">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-lg"
          style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>
          <Icon size={15} className="text-primary" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="text-on-surface text-[0.875rem]" style={fvs(550)}>{title}</span>
            {done && (
              <span className="inline-flex shrink-0 items-center gap-1 text-[0.75rem]" style={{ color: 'var(--color-success)' }}>
                <Check size={13} aria-hidden="true" /> Done
              </span>
            )}
          </div>
          <p className="mt-0.5 text-on-surface-low text-[0.8125rem]">{blurb}</p>
        </div>
        {!done && (
          <Button variant="tonal" size="sm" loading={state.phase === 'running'}
            onClick={onRun}>
            {state.phase === 'failed' ? 'Try again' : action}
          </Button>
        )}
      </div>

      {state.phase === 'done' && <Outcome outcome={state.outcome} onExitTo={onExitTo} />}
      {state.phase === 'failed' && (
        <Failure message={state.message} target={state.target} onExitTo={onExitTo} />
      )}
    </motion.div>
  )
}

/** What actually happened, read back off the real responses. */
function Outcome({ outcome, onExitTo }: { outcome: TryOneOutcome; onExitTo: (path: string) => void }) {
  return (
    <div className="mt-3 flex flex-col gap-1.5 border-t border-outline-variant pt-3">
      <p className="text-[0.8125rem]" style={{ color: 'var(--color-success)' }}>{outcome.headline}</p>
      <dl className="flex flex-col gap-1">
        {outcome.facts.map((f) => (
          <div key={f.label} className="flex gap-2 text-[0.75rem]">
            <dt className="w-[5.5rem] shrink-0 text-on-surface-low">{f.label}</dt>
            <dd className="min-w-0 flex-1 break-words text-on-surface-var">{f.value}</dd>
          </div>
        ))}
      </dl>
      {/* Opening the thing you just made means leaving setup, so it goes through the
          same guard-handoff the failure path uses rather than a bare hash link that
          the route guard would bounce. */}
      <TextLink size="xs" icon={ExternalLink} iconPosition="trailing" className="mt-0.5 self-start"
        onClick={() => onExitTo(outcome.href)}>
        {outcome.linkLabel}
      </TextLink>
    </div>
  )
}

/** The provider-passed-its-test-then-refused-the-call branch (and every other real
 *  failure). Shows the gateway's own words, never a paraphrase, and always offers a
 *  way out of the flow to the surface that owns the problem. */
function Failure({ message, target, onExitTo }: {
  message: string; target: SettingsTarget; onExitTo: (path: string) => void
}) {
  return (
    <div className="mt-3 flex flex-col gap-2 border-t border-outline-variant pt-3">
      <InlineError icon multiline>{message}</InlineError>
      <p className="text-on-surface-low text-[0.75rem]">{target.because}</p>
      <div>
        <Button variant="tonal" size="sm" onClick={() => onExitTo(target.path)}>
          {target.label}
        </Button>
      </div>
      <p className="text-on-surface-low text-[0.75rem]">
        This finishes setup and takes you there — you can come back to these any time from Discover.
      </p>
    </div>
  )
}
