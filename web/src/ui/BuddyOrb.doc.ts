import type { UiDoc } from './uiDoc'

// Doc object for BuddyOrb — the desktop buddy's one orb, the agent orb's halftone drawing for its
// state, with its state's exact line beside it (docs/design/buddy/character.md).
const doc: UiDoc = {
  name: 'BuddyOrb',
  keywords: ['buddy', 'orb', 'character', 'status', 'family', 'halftone', 'dots', 'plate', 'tray', 'companion', 'presence', 'alert'],
  description:
    "The desktop buddy's orb: the agent orb's halftone drawing for each state it shows (ui/buddy/buddyHalftone.ts: a phase is the agent orb's own state, but the buddy's listening, a push-to-talk capture, is the recording dot; a restart is retrying and the gateway away offline), and the state's exact line beside it, so the art never carries the state alone. On the desktop's window it sits on a disc plate of its own, the surface with a hairline and a soft shadow, so it reads over any wallpaper. Its inks are the orb tokens and the status tones, and a scheme change retints it within a frame. A change of state is the agent orb's own: every dot moves on the smooth spring, and done swirls and bumps once on arrival. It paints only while its dots or the morph move, at most 30 frames a second: a still state draws nothing more. Under reduced motion every state is its still and a change swaps (data-buddy-orb=still). The buddy window, the playground, the focused card and the phone's views all draw it.",
  props: [
    { name: 'state', description: "What the orb shows: a phase of the phase contract (lib/workPhase.ts), or 'restarting' or 'offline'. The state machine's truth maps onto it through orbStateOf (ui/buddy/orbPoses.ts)." },
    { name: 'line', description: "The state's exact line, from lib/buddy/copy.ts, cut to the surface; shown beside the orb." },
    { name: 'size', description: '18, 24, 48 (default) or 96. Below 30 px the dots are drawn larger and a plate state gives them up to its mark, as the agent orb’s are.' },
    { name: 'still', description: 'Hold even a moving state still, as a contact sheet’s frame.' },
    { name: 'plate', description: 'Sit on the disc plate: the desktop’s window, over wallpaper and other windows. Off elsewhere, where the orb sits on the app’s own surface.' },
  ],
  bestPractices: [
    { guidance: true, description: "Pass the machine's line through lib/buddy/lines.ts, so the same truth says the same words on every surface." },
    { guidance: true, description: 'Draw one buddy orb per surface; the in-sight row’s orbs are the agent orb.' },
    { guidance: false, description: 'Do not recolour it or tint the whole orb with an alert colour: alert tones are its plate’s mark only.' },
    { guidance: false, description: 'Do not animate it on a timer of the page’s own: it moves when its state does.' },
  ],
  anatomy: [
    'span.inline-flex[data-buddy-orb=animated|still][data-buddy-state][data-buddy-family][data-buddy-scene][data-buddy-moving][data-buddy-plate?]',
    'canvas[aria-hidden] — the plate, the dots and the plate state’s mark, painted with the orb and tone tokens',
    'span[data-buddy-line][data-type=body-s] — the exact line, beside the orb',
  ],
}

export default doc
