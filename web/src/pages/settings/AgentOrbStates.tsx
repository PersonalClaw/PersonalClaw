import { useId, useState } from 'react'
import { Orbit } from 'lucide-react'
import { AgentOrb } from '../../ui/AgentOrb'
import { Button } from '../../ui/Button'
import { Eyebrow } from '../../ui/Eyebrow'
import { WORK_PHASES, phaseLabel } from '../../lib/workPhase'
import { ORB_STATE_OF, type OrbStateName } from '../../lib/orbState'
import { Section, useHeadingTag } from './settingsUI'

// ── Settings → Design: the agent orb's states ───────────────────────────────────────────────────
//
// The orb is the agent's working signal, tinted by the scheme (`docs/design/agent-orb.md`), so an
// owner choosing one sees here what it does to the orb: every phase of the contract under the one
// of the claw ring's five states it is drawn as, at the three sizes it is drawn at: 64 px in an
// empty chat, 20 px in a turn's live line and 12 px as a list row's still mark. The words name
// each phase; the orbs are its picture. It stays closed until asked for, because thinking and
// working move and a page with nothing running animates nothing. The visual suite photographs it
// for every phase (`e2e/visual.spec.ts`).

const STATES: { state: OrbStateName; title: string }[] = [
  { state: 'ready', title: 'Ready' },
  { state: 'thinking', title: 'Thinking' },
  { state: 'working', title: 'Working' },
  { state: 'waiting', title: 'Waiting on you' },
  { state: 'done', title: 'Done' },
]

export function AgentOrbStates() {
  const stateHeading = useHeadingTag(3)
  const [open, setOpen] = useState(false)
  const id = useId()
  return (
    <Section title="Agent orb" icon={Orbit} iconTone="muted"
      hint="What the agent shows while it works, in this scheme's colours: each state at 64 px, as in an empty chat, at 20 px, as in a turn's live line, and at 12 px, as a list row's still mark."
      right={(
        <Button variant="ghost" size="sm" ariaExpanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? 'Hide its states' : 'Show its states'}
        </Button>
      )}>
      {open && (
        <div data-orb-gallery className="flex flex-col gap-l bg-surface-container p-m rounded-lg">
          {STATES.map(({ state, title }) => (
            <div key={state} role="group" aria-labelledby={`${id}-${state}`} data-orb-sheet-state={state}>
              <Eyebrow as={stateHeading} id={`${id}-${state}`} className="mb-s">{title}</Eyebrow>
              <div className="grid grid-cols-[repeat(auto-fill,minmax(8.5rem,1fr))] gap-s">
                {WORK_PHASES.filter((phase) => ORB_STATE_OF[phase].state === state).map((phase) => (
                  <div key={phase} data-orb-sheet-phase={phase} className="flex flex-col items-center gap-s px-s py-m">
                    <span aria-hidden="true" className="inline-flex"><AgentOrb phase={phase} size={64} /></span>
                    <span className="flex max-w-full items-center gap-xs">
                      <span aria-hidden="true" className="inline-flex"><AgentOrb phase={phase} /></span>
                      <span aria-hidden="true" className="inline-flex"><AgentOrb phase={phase} size={12} /></span>
                      <span data-type="caption" className="min-w-0 text-on-surface-var">{phaseLabel(phase)}</span>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Section>
  )
}
