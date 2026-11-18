import { useId, useState } from 'react'
import { Orbit } from 'lucide-react'
import { AgentOrb } from '../../ui/AgentOrb'
import { Button } from '../../ui/Button'
import { Eyebrow } from '../../ui/Eyebrow'
import type { RunAct } from '../../lib/api'
import { RUN_ACTS, WORK_PHASES, phaseLabel, type WorkPhase } from '../../lib/workPhase'
import { ORB_SCENE_OF, ORB_STAGES, ORB_STAGE_LABELS, TOOL_SCENE_OF_ACT, type OrbScene, type OrbStage } from '../../lib/orbState'
import { Section, useHeadingTag } from './settingsUI'

// ── Settings → Design: the agent orb's states ───────────────────────────────────────────────────
//
// The orb is the agent's working signal, tinted by the scheme (`docs/design/agent-orb.md`), so an
// owner choosing one sees here what it does to the orb: every state it can show, in its family, at
// the three sizes it is drawn at: 64 px in an empty chat and voice mode, 20 px in a turn's live line
// and 12 px as a list row's still mark. Each state lists what drives it, a phase of the contract (a
// tool step by what its call does) or a stage a page names, in words; the orbs are its picture. It stays closed until asked for, because
// most states move and a page with nothing running animates nothing. The visual suite photographs
// it for every state (`e2e/visual.spec.ts`).

/** The states by family, as the design groups them. */
const FAMILIES: { title: string; scenes: { scene: OrbScene; title: string }[] }[] = [
  { title: 'Quiet', scenes: [{ scene: 'ready', title: 'Ready' }, { scene: 'done', title: 'Done' }] },
  { title: 'Thinking', scenes: [{ scene: 'queued', title: 'Queued' }, { scene: 'thinking', title: 'Thinking' }, { scene: 'planning', title: 'Planning' }] },
  {
    title: 'Working',
    scenes: [
      { scene: 'searching', title: 'Searching' }, { scene: 'reading', title: 'Reading' }, { scene: 'editing', title: 'Editing' },
      { scene: 'running', title: 'Running a command' },
      { scene: 'delegating', title: 'Delegating' }, { scene: 'answering', title: 'Answering' }, { scene: 'retrying', title: 'Retrying' },
    ],
  },
  { title: 'Needs you', scenes: [{ scene: 'approval', title: 'Needs your OK' }, { scene: 'question', title: 'Asks you' }] },
  {
    title: 'Voice',
    scenes: [
      { scene: 'listening', title: 'Listening' }, { scene: 'recording', title: 'Recording' },
      { scene: 'transcribing', title: 'Transcribing' }, { scene: 'speaking', title: 'Speaking' },
    ],
  },
  { title: 'Stopped', scenes: [{ scene: 'error', title: 'Error' }, { scene: 'offline', title: 'Offline' }] },
]

type Driver = { phase: WorkPhase; act: RunAct | null; stage: OrbStage | null; words: string; key: string }

/** The words for a tool step whose call says what it does, beside the phase's own. */
const ACT_WORDS: Readonly<Record<RunAct, string>> = { read: 'a call that reads', edit: 'a call that edits', run: 'a call that runs' }

/** What drives `scene`: each phase drawn as it, each tool step whose call does what it draws (one
 *  that runs is drawn as a tool step that says nothing is, so it is that phase's), and each stage
 *  that is it. */
function driversOf(scene: OrbScene): Driver[] {
  const phases: Driver[] = WORK_PHASES.filter((p) => ORB_SCENE_OF[p] === scene).map((p) => ({ phase: p, act: null, stage: null, words: phaseLabel(p), key: p }))
  const acts: Driver[] = RUN_ACTS.filter((a) => TOOL_SCENE_OF_ACT[a] === scene && scene !== ORB_SCENE_OF.tool)
    .map((a) => ({ phase: 'tool', act: a, stage: null, words: `${phaseLabel('tool')}: ${ACT_WORDS[a]}`, key: `tool-${a}` }))
  const stages: Driver[] = ORB_STAGES.filter((s) => s === scene).map((s) => ({ phase: 'idle', act: null, stage: s, words: ORB_STAGE_LABELS[s], key: s }))
  return [...phases, ...acts, ...stages]
}

export function AgentOrbStates() {
  const familyHeading = useHeadingTag(3)
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
          {FAMILIES.map((family) => (
            <div key={family.title} role="group" aria-labelledby={`${id}-${family.title}`} data-orb-sheet-family={family.title}>
              <Eyebrow as={familyHeading} id={`${id}-${family.title}`} className="mb-s">{family.title}</Eyebrow>
              <div className="grid grid-cols-[repeat(auto-fill,minmax(8.5rem,1fr))] gap-s">
                {family.scenes.flatMap(({ scene, title }) => driversOf(scene).map((d) => (
                  <div key={d.key} role="group" aria-labelledby={`${id}-${d.key}`} data-orb-sheet-state={scene} data-orb-sheet-phase={d.stage ?? d.phase} {...(d.act ? { 'data-orb-sheet-act': d.act } : {})}
                    className="flex flex-col items-center gap-s px-s py-m">
                    <span aria-hidden="true" className="inline-flex"><AgentOrb phase={d.phase} act={d.act} stage={d.stage} size={64} /></span>
                    <span className="flex max-w-full items-center gap-xs">
                      <span aria-hidden="true" className="inline-flex"><AgentOrb phase={d.phase} act={d.act} stage={d.stage} /></span>
                      <span aria-hidden="true" className="inline-flex"><AgentOrb phase={d.phase} act={d.act} stage={d.stage} size={12} /></span>
                      <span className="flex min-w-0 flex-col">
                        <span id={`${id}-${d.key}`} data-type="caption" className="min-w-0 text-on-surface">{title}</span>
                        <span data-type="caption" className="min-w-0 text-on-surface-var">{d.words}</span>
                      </span>
                    </span>
                  </div>
                )))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Section>
  )
}
