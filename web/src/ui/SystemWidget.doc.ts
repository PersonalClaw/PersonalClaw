import type { UiDoc } from './uiDoc'

// Doc object for SystemWidget — the status line's gateway connection + system card.
// It reads the shell's connection reading and live endpoints, and takes no props.
const doc: UiDoc = {
  name: 'SystemWidget',
  keywords: ['system', 'health', 'status', 'connectivity', 'gateway', 'cpu', 'memory', 'agents', 'restart'],
  description:
    "Live system + auth health as the status line's gateway connection, at the window's bottom left. Collapsed it is a still dot — GREEN = gateway connected, ORANGE = connecting, RED = disconnected — with the state in words beside it while not connected (when the gateway was last reached, once lost), read from the shell's one connection reading (app/gatewayConnection). Click it for the full card (CPU/mem/disk/GPU bars, network, processes, background-agent monitor, gateway Restart / Update & Restart controls, and auth), opening up from the line. Takes no props.",
  props: [
    { name: 'className', description: "Extra classes on the status trigger, e.g. the compact widths' 44 px hit box (`TOUCH_TARGET`) the shell gives it." },
  ],
  bestPractices: [
    { guidance: true, description: 'Mount it once, in the status line (app/StatusLine), under the shell\'s GatewayConnectionProvider — the connection is that provider\'s reading, and the card reads /api/system only while open, so do not feed it data or wrap it in another poller.' },
    { guidance: true, description: 'Preserve the dot semantics: its color reflects gateway CONNECTIVITY (read success/fail), not CPU/mem pressure, and it holds still in every state.' },
    { guidance: true, description: 'Format every host reading through lib/readings: the gateway leaves out a reading its probe could not take, so any field can be missing on one poll.' },
    { guidance: false, description: 'Do not add vendor-specific status (e.g. Ollama) to the card — the widget deliberately omits it to stay provider-agnostic.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens.' },
  ],
  anatomy: ['connection trigger (a still dot; the state in words while not connected)', 'portaled fixed card (anchored up + right from the status line)', 'system readings: CPU / Memory / Disk bars + GPU / Network / Processes KVs (a reading missing from a poll shows the placeholder), in their own WidgetBoundary', 'RunningAgents (background subagent monitor), in its own WidgetBoundary', 'RestartControls (Restart / Update & Restart, warn-if-active confirm)', 'auth status footer'],
}

export default doc
