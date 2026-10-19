import { useId, useState } from 'react'
import { Orbit } from 'lucide-react'
import { AgentOrb } from '../../ui/AgentOrb'
import { Button } from '../../ui/Button'
import { Eyebrow } from '../../ui/Eyebrow'
import { WORK_PHASES, phaseFamily, phaseLabel, type PhaseFamily } from '../../lib/workPhase'
import { Section } from './settingsUI'

// ── Settings → Design: the agent orb's states ───────────────────────────────────────────────────
//
// The orb is the agent's working signal, tinted by the scheme (`docs/design/agent-orb.md`), so an
// owner choosing one sees here what it does to the orb: every phase of the contract at the two
// sizes it is drawn at, 20 px at the head of a reply and 64 px in an empty chat, under the family
// it reads as. The words name each state; the orbs are its picture. It stays closed until asked
// for, because the active family moves and a page with nothing running animates nothing. The
// visual suite photographs it for every state (`e2e/visual.spec.ts`).

const FAMILIES: { family: PhaseFamily; title: string }[] = [
  { family: 'active', title: 'Working' },
  { family: 'waiting', title: 'Waiting on you' },
  { family: 'blocked', title: 'Stopped' },
  { family: 'quiet', title: 'At rest' },
]

export function AgentOrbStates() {
  const [open, setOpen] = useState(false)
  const id = useId()
  return (
    <Section title="Agent orb" icon={Orbit} iconTone="muted"
      hint="What the agent shows while it works, in this scheme's colours: each state at 20 px, as at the head of a reply, and at 64 px, as in an empty chat."
      right={(
        <Button variant="ghost" size="sm" ariaExpanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? 'Hide its states' : 'Show its states'}
        </Button>
      )}>
      {open && (
        <div data-orb-gallery className="flex flex-col gap-l bg-surface-container p-m rounded-lg">
          {FAMILIES.map(({ family, title }) => (
            <div key={family} role="group" aria-labelledby={`${id}-${family}`} data-orb-family={family}>
              <Eyebrow as="h3" id={`${id}-${family}`} className="mb-s">{title}</Eyebrow>
              <div className="grid grid-cols-[repeat(auto-fill,minmax(8.5rem,1fr))] gap-s">
                {WORK_PHASES.filter((phase) => phaseFamily(phase) === family).map((phase) => (
                  <div key={phase} data-orb-state={phase} className="flex flex-col items-center gap-s rounded-lg bg-surface px-s py-m">
                    <span aria-hidden="true" className="inline-flex"><AgentOrb phase={phase} size={64} /></span>
                    <span className="flex max-w-full items-center gap-xs">
                      <span aria-hidden="true" className="inline-flex"><AgentOrb phase={phase} /></span>
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
