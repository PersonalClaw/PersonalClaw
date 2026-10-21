import type { UiDoc } from './uiDoc'

// Doc object for AgentOrb — what an agent's turn is doing, as one small orb of dots driven only by
// the phase contract (docs/design/agent-orb.md).
const doc: UiDoc = {
  name: 'AgentOrb',
  keywords: ['orb', 'thinking', 'working', 'indicator', 'spinner', 'loading', 'phase', 'agent', 'turn', 'status', 'family', 'approval', 'streaming'],
  description:
    "What an agent's turn is doing, as one small orb of dots. It takes a phase of the phase contract (lib/workPhase.ts) and maps it to one drawing in one of the four families: the active family animates (its drawings are variants, and the words beside it name the phase), while waiting, blocked and quiet are still frames, a waiting or failed turn carrying a '!', '?' or '×' badge in the phase's tone. Its dots are --color-orb-alive while an agent works or waits on the owner and --color-orb-rest where nothing is alive; it takes the words' ink under prefers-contrast: more and the system text colour in forced colours. It schedules animation frames only for live work, on screen, in a visible tab, with motion allowed, and then at most 30 a second, through the loop the buddy's dots share. role=\"img\", named for the phase.",
  props: [
    { name: 'phase', description: "The turn's phase from the phase contract (a WorkPhase). Pages pass a phase, never an orb state or a status line's words, so no page can show a state the gateway did not measure." },
    { name: 'size', description: '20 (default) at the head of a reply; 64 only for empty states, voice mode and the buddy’s expanded view. The two are separately tuned drawings, never one scaled.' },
    { name: 'className', description: 'Layout utilities only (margin, alignment). The colour is the orb tokens; never recolour it.' },
  ],
  bestPractices: [
    { guidance: true, description: "Put the phase's words beside it (the gateway's line, or phaseLabel(phase)) in a polite live region: the orb is an image named once, and the line says what changes." },
    { guidance: true, description: 'Show one orb per turn, at the head of its reply; let it settle when the turn ends.' },
    { guidance: false, description: 'Do not animate an orb with nothing running: an empty chat’s 64 px mark is the idle phase’s still frame. Never put one in a sidebar row: a working thread’s row has a static pip.' },
    { guidance: false, description: 'Do not derive the phase from status text, a timer or a flag of the page’s own.' },
  ],
  anatomy: [
    'span.relative.inline-block[data-agent-orb=animated|instant|still][data-orb-phase][data-orb-family]',
    'canvas[role=img][aria-label=<phase label>] — the thinking-orbs drawing, tinted with the orb tokens',
    'span[data-orb-badge][aria-hidden] — approval "!", input "?", error "×", ringed in the phase tone',
  ],
}

export default doc
