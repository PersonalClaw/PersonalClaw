import type { UiDoc } from './uiDoc'

// Doc object for StateMark — a state's family mark beside the words that name it, from the shared
// brand and state tokens (#3809). The rule it encodes: colour never carries a state alone.
const doc: UiDoc = {
  name: 'StateMark',
  keywords: ['state', 'status', 'phase', 'family', 'quiet', 'active', 'waiting', 'blocked', 'pip', 'dot', 'running', 'needs you', 'failed'],
  description:
    "A state shown the one way every surface shows it: a small mark in its family's shared state token (`--color-state-quiet|active|waiting|blocked`) and the words that name the state. The family carries urgency and the words carry the exact state, so the mark is aria-hidden and the label is required; the agent orb and the buddy read the same tokens, so a scheme recolours them together.",
  props: [
    { name: 'family', description: "The state's family from the phase contract: 'quiet' | 'active' | 'waiting' | 'blocked'. Take it from phaseFamily(phase) in lib/workPhase.ts, never choose it for its colour." },
    { name: 'children', description: "The words that name the state — phaseLabel(phase), or the work's own one-line description. Required and never empty: the mark alone means nothing." },
    { name: 'id', description: 'Optional id, for a control that names this state with aria-describedby.' },
    { name: 'className', description: 'Layout utilities only (margin, alignment). The colour is the family token; never recolor the mark.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Use StateMark for a row or card that says which family a piece of work is in (running, needs you, failed, at rest); give it the phase label or the work’s own line.' },
    { guidance: true, description: 'Pick the family from the phase contract so a phase reads in the same family on every surface (chat, the tray, the buddy, the phone).' },
    { guidance: false, description: 'Do not show a state by the mark alone or by colour alone: in forced colours the fill is gone and only the ring and the words remain.' },
    { guidance: false, description: 'Do not paint the mark from a status tone or a literal; the family token is the shared one the orb and the buddy read.' },
  ],
  anatomy: ['span.inline-flex.items-center.gap-xs.text-on-surface[data-state-family]', 'span.size-2.rounded-pill.border[aria-hidden] — fill and ring in var(--color-state-<family>)', 'span — the label'],
}

export default doc
