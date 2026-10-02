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
    { guidance: true, description: 'Format every host reading through lib/readings: the gateway leaves out a reading its probe could not take, so any field can be missing on one poll.' },
    { guidance: false, description: 'Do not add vendor-specific status (e.g. Ollama) to the card — the widget deliberately omits it to stay provider-agnostic.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens.' },
  ],
  anatomy: ['connectivity dot trigger (outward pulse ring while connected)', 'portaled fixed card (anchored down + left from the corner)', 'system readings: CPU / Memory / Disk bars + GPU / Network / Processes KVs (a reading missing from a poll shows the placeholder), in their own WidgetBoundary', 'RunningAgents (background subagent monitor), in its own WidgetBoundary', 'RestartControls (Restart / Update & Restart, warn-if-active confirm)', 'auth status footer'],
}

export default doc
