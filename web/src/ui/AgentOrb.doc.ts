import type { UiDoc } from './uiDoc'

// Doc object for AgentOrb — what an agent's turn is doing, as the halftone orb, driven only by the
// phase contract and a page's typed stages (docs/design/agent-orb.md).
const doc: UiDoc = {
  name: 'AgentOrb',
  keywords: ['orb', 'thinking', 'working', 'indicator', 'spinner', 'loading', 'phase', 'agent', 'turn', 'status', 'state', 'approval', 'streaming', 'halftone', 'dots', 'mark'],
  description:
    "What an agent's turn is doing, as the halftone orb: a sphere of 272 dots, tilted toward you. It takes a phase of the phase contract (lib/workPhase.ts), and a typed stage where a page has one (recording, transcribing, speaking, offline), and draws it as one of twenty states through the one mapping (lib/orbState.ts): ready, the sphere, still; thinking, a ripple out from the centre; done, needs your OK, asks you, error and offline, the orb stepped aside with a check, '!', '?', '×' or a broken link on a small plate, still; and for each kind of work and each voice stage a scene the dots fly out to become (a globe charting a route, a scan line over the globe, a command's output, helpers fed over links, a bubble filling, an attempt circling, an audio graph, the recording dot, words coming out of sound, arcs of voice). Done swirls once and bumps only when the turn completed. A change of state moves every dot on the smooth spring, with a short burst mid-change, and is an instant swap under reduced motion. Its inks are --color-orb-alive for what is active and --color-orb-rest for what is passive, with the status tones only where a state names one; it takes the words' ink under prefers-contrast: more and the system text colour in forced colours. It schedules animation frames only while its state moves or morphs, on screen, in a visible tab, with motion allowed, and then at most 30 a second, through the capped loop. role=\"img\", named for the phase or the stage.",
  props: [
    { name: 'phase', description: "The turn's phase from the phase contract (a WorkPhase). Pages pass a phase, never an orb state or a status line's words, so no page can show a state the gateway did not measure." },
    { name: 'stage', description: "What holds that is not a phase, typed: 'recording' (a push-to-talk capture), 'transcribing', 'speaking' (voice mode), 'offline' (the gateway away). It wins over the phase while it is set, and names the image." },
    { name: 'size', description: "20 (default) in the turn's live line and the history peek; 64 for the empty chat and voice mode; 12 as a list row's still mark, as in the buddy card's rows, which never moves." },
    { name: 'className', description: 'Layout utilities only (margin, alignment). The colour is the orb tokens; never recolour it.' },
    { name: 'bloom', description: "Whether arriving at done may play its one swirl and bump (the default). Read when the change starts: the turn's live line, the history peek's head and voice mode pass it only for a turn that completed, so a stopped or declined one settles into done without it." },
    { name: 'onSettled', description: "Called with the state it has settled on: when a change's morph or done's swirl ends, at once for a change that swaps, and on its first paint. The turn's live line and the history peek's head hold a turn's ending until it is called." },
  ],
  bestPractices: [
    { guidance: true, description: "Put the phase's words beside it (the gateway's line, or phaseLabel(phase)) in a polite live region: the orb is an image named once, and the line says what changes." },
    { guidance: true, description: 'Show one orb per turn, in its live line; let it settle when the turn ends.' },
    { guidance: false, description: 'Do not animate an orb with nothing running: an empty chat\'s 64 px orb is the idle phase\'s still sphere. A sidebar row takes OrbMark, the still 12 px mark of its pip\'s state, never a moving orb.' },
    { guidance: false, description: 'Do not derive the phase from status text, a timer or a flag of the page\'s own.' },
  ],
  anatomy: [
    'span.relative.inline-block[data-agent-orb=animated|instant|still][data-orb-phase][data-orb-family][data-orb-state][data-orb-scene][data-orb-stage?][data-orb-change=none|morph|bloom][data-orb-badge?][data-orb-glyph?]',
    'canvas.pointer-events-none[role=img][aria-label=<phase or stage label>] — the halftone orb (ui/orb/halftone.ts, ui/orb/scenes.ts), its plate mark (ui/orb/plateMarks.ts), reaching past the box on each side for the burst and the plate\'s shadow',
  ],
}

export default doc
