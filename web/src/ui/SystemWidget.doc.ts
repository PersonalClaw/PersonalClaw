import type { UiDoc } from './uiDoc'

// Doc object for SystemWidget — the shell-corner connectivity dot + system card.
// It reads live from stores/endpoints and takes no props.
const doc: UiDoc = {
  name: 'SystemWidget',
  keywords: ['system', 'health', 'status', 'connectivity', 'gateway', 'cpu', 'memory', 'agents', 'restart'],
  description:
    "Live system + auth health as the app shell's top-right connectivity dot. Collapsed it is a single dot — GREEN + pulsing = gateway connected, ORANGE = connecting / unknown, RED = disconnected — driven by the /api/system poll succeeding vs failing. Click it for the full card (CPU/mem/disk/GPU bars, network, processes, background-agent monitor, gateway Restart / Update & Restart controls, and auth). Takes no props; reads live from the system + auth endpoints.",
  props: [],
  bestPractices: [
    { guidance: true, description: 'Mount it once in the shell corner cluster (ShellCornerRight) — it self-polls via useVisiblePoll, so do not feed it props or wrap it in another poller.' },
    { guidance: true, description: 'Preserve the dot semantics: its color reflects gateway CONNECTIVITY (poll success/fail), not CPU/mem pressure.' },
    { guidance: false, description: 'Do not add vendor-specific status (e.g. Ollama) to the card — the widget deliberately omits it to stay provider-agnostic.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens.' },
  ],
  anatomy: ['connectivity dot trigger (outward pulse ring while connected)', 'portaled fixed card (anchored down + left from the corner)', 'system bars (CPU / Memory / Disk) + GPU / Network / Processes KVs', 'RunningAgents (background subagent monitor)', 'RestartControls (Restart / Update & Restart, warn-if-active confirm)', 'auth status footer'],
}

export default doc
