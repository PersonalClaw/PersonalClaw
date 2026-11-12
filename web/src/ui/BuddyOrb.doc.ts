import type { UiDoc } from './uiDoc'

// Doc object for BuddyOrb — the desktop buddy's one orb, the agent orb's claw ring moved by the buddy's
// pose, with its state's exact line beside it (docs/design/buddy/character.md).
const doc: UiDoc = {
  name: 'BuddyOrb',
  keywords: ['buddy', 'orb', 'character', 'status', 'family', 'badge', 'wash', 'tray', 'companion', 'presence', 'alert'],
  description:
    "The desktop buddy's orb: the agent orb's claw ring for each state (lib/orbState.ts, ui/orb/clawRing.ts), moved by the buddy's pose for it (buddyRig/poseSet.ts: the strokes' weight, how far they sit from the centre and how far they sag, the whole orb's rise or fall, a badge and an alert's wash in strokes in the alert's tone), and the state's exact line beside it, so the art never carries the state alone. Its strokes are --color-orb-alive while an agent works or waits on the owner and --color-orb-rest where nothing is, and a scheme change retints it within a frame. A change of state morphs as the agent orb's does, and done keeps the open ring with its check, with no bloom. It moves between poses on the app's presets, scaled by the bounciness and expressiveness dials, and paints only while its strokes, the morph or its pose move, at most 30 frames a second: a pose that holds draws nothing. Under reduced motion every state is its still (data-buddy-orb=still). The buddy window, the playground, the focused card and the phone's views all draw it.",
  props: [
    { name: 'state', description: "What the orb shows: a phase of the phase contract (lib/workPhase.ts), or 'restarting' or 'offline'. The state machine's truth maps onto it through orbStateOf (ui/buddy/orbPoses.ts)." },
    { name: 'line', description: "The state's exact line, from lib/buddy/copy.ts, cut to the surface; shown beside the orb." },
    { name: 'size', description: '18, 24, 48 (default) or 96. The strokes are drawn wider at 20 px and below, as the agent orb’s are; below 24 the badge drops its ring, and a waiting badge is solid.' },
    { name: 'still', description: 'Hold even a moving state still, as a contact sheet’s frame.' },
  ],
  bestPractices: [
    { guidance: true, description: "Pass the machine's line through lib/buddy/lines.ts, so the same truth says the same words on every surface." },
    { guidance: true, description: 'Draw one buddy orb per surface; the in-sight row’s orbs are the agent orb.' },
    { guidance: false, description: 'Do not recolour it or tint the whole orb with an alert colour: alert tones are its badge and wash only.' },
    { guidance: false, description: 'Do not animate it on a timer of the page’s own: it moves when its state does.' },
  ],
  anatomy: [
    'span.inline-flex[data-buddy-orb=animated|still][data-buddy-state][data-buddy-family][data-buddy-moving][data-buddy-badge]',
    'canvas[role=img][aria-label=<state words>] — the strokes, the wash and the badge, painted with the orb and tone tokens',
    'span[data-buddy-line][data-type=body-s] — the exact line, beside the orb',
  ],
}

export default doc
