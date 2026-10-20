import type { UiDoc } from './uiDoc'

// Doc object for UpdateProgressOverlay — the single shell-level surface that renders
// self-update / restart progress. Driven by `update_progress` WS events; no props.
const doc: UiDoc = {
  name: 'UpdateProgressOverlay',
  keywords: ['update', 'restart', 'progress', 'overlay', 'stepper', 'gateway', 'self-update', 'shell'],
  description:
    'The one shell-level surface that renders self-update / restart progress. A modal overlay that appears on the first `update_progress` WS step from ANY page, tracks the pipeline live (pulling → installing → building → restarting), and shows either the 4-step stepper (full update) or a single "Restarting gateway" spinner (plain restart). Offers Cancel only while the update moves or installs, which is what the gateway stops (POST /api/update/cancel): the sheet stays open until the gateway answers, then shows what the stop left. Past that point it says the update will finish and offers Hide; a plain restart offers Hide. Dismiss (POST /api/update/dismiss) closes a failed or cancelled update and never stops one. Hydrates from /api/status so a page opened mid-update still shows it. Mount once; takes no props.',
  props: [],
  bestPractices: [
    { guidance: true, description: 'Mount UpdateProgressOverlay exactly once in the app shell (next to Toaster / DialogHost) — it is the single progress surface.' },
    { guidance: true, description: 'Let it own ALL update/restart feedback — trigger updates via /api/update and restarts via /api/system/restart and it picks them up over the WS.' },
    { guidance: false, description: 'Do not render inline restart/update spinners elsewhere — they can never clear across the gateway re-exec (the component unmounts), so they read as permanently stuck.' },
    { guidance: false, description: 'A pipeline-START failure (e.g. 409 dirty tree) pushes no progress event, so the overlay never opens — surface that error via a toast at the call site, not here.' },
    { guidance: false, description: 'Do not offer Cancel, or close the sheet on Cancel, where the gateway cannot stop the update: once it has installed the new release it builds and restarts, and a Cancel there claimed a stop that did not happen.' },
  ],
  anatomy: ['portaled fixed overlay + backdrop blur', 'centered sheet (role="alertdialog")', 'header (adaptive title/icon: update vs restart vs done vs error)', 'what it says, in a polite and an assertive live region', '4-step StepRow stepper (full update only)', 'Cancel / Hide / Dismiss button'],
}

export default doc
