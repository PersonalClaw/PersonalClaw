import type { UiDoc } from './uiDoc'

// Doc object for AgentOrb — what an agent's turn is doing, as the claw ring, driven only by the
// phase contract (docs/design/agent-orb.md).
const doc: UiDoc = {
  name: 'AgentOrb',
  keywords: ['orb', 'thinking', 'working', 'indicator', 'spinner', 'loading', 'phase', 'agent', 'turn', 'status', 'state', 'approval', 'streaming', 'claw ring', 'mark'],
  description:
    "What an agent's turn is doing, as the claw ring: six curved strokes drawn like the brand mark's two, around a soft centre. It takes a phase of the phase contract (lib/workPhase.ts) and draws it as one of five states through the one mapping (lib/orbState.ts): ready, the open ring, still; thinking, the strokes swelling and drawing back; working, two rings turning in opposite directions; waiting on you, the strokes locked in a clasp, still, with a '!' or '?' badge in the phase's tone; done, the strokes closed into the mark, with one success bloom when the turn completed. A failure is done's form with a '×' badge and no bloom, and a turn stopped or declined closes to done's form with no bloom. A change of state morphs on the smooth spring, and is an instant swap under reduced motion. Its strokes are --color-orb-alive while an agent works or waits on the owner and --color-orb-rest where nothing is alive; it takes the words' ink under prefers-contrast: more and the system text colour in forced colours. It schedules animation frames only while it thinks, works or morphs, on screen, in a visible tab, with motion allowed, and then at most 30 a second, through the loop the buddy's dots share. role=\"img\", named for the phase.",
  props: [
    { name: 'phase', description: "The turn's phase from the phase contract (a WorkPhase). Pages pass a phase, never an orb state or a status line's words, so no page can show a state the gateway did not measure." },
    { name: 'size', description: "20 (default) in the turn's live line and the history peek; 64 for the empty chat and voice mode; 12 as a list row's still mark, as in the buddy card's rows, which never moves." },
    { name: 'className', description: 'Layout utilities only (margin, alignment). The colour is the orb tokens; never recolour it.' },
    { name: 'bloom', description: "Whether arriving at done may play its one success bloom (the default). Read when the change starts: the turn's live line, the history peek's head and voice mode pass it only for a turn that completed, so a stopped or declined one closes to done's form without it." },
    { name: 'onSettled', description: "Called with the orb state it has settled on: when a change's morph or done's bloom ends, at once for a change that swaps, and on its first paint. The turn's live line and the history peek's head hold a turn's ending until it is called." },
  ],
  bestPractices: [
    { guidance: true, description: "Put the phase's words beside it (the gateway's line, or phaseLabel(phase)) in a polite live region: the orb is an image named once, and the line says what changes." },
    { guidance: true, description: 'Show one orb per turn, in its live line; let it settle when the turn ends.' },
    { guidance: false, description: 'Do not animate an orb with nothing running: an empty chat\'s 64 px orb is the idle phase\'s still form. A sidebar row takes OrbMark, the still 12 px mark of its pip\'s state, never a moving orb.' },
    { guidance: false, description: 'Do not derive the phase from status text, a timer or a flag of the page\'s own.' },
  ],
  anatomy: [
    'span.relative.inline-block[data-agent-orb=animated|instant|still][data-orb-phase][data-orb-family][data-orb-state][data-orb-change=none|morph|bloom]',
    'canvas[role=img][aria-label=<phase label>] — the claw ring (ui/orb/clawRing.ts), painted in the orb tokens (ui/orb/paintRing.ts)',
    'span[data-orb-badge][aria-hidden] — approval "!", input "?", error "×", ringed in the phase tone',
  ],
}

export default doc
