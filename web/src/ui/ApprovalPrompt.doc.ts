import type { UiDoc } from './uiDoc'

const doc: UiDoc = {
  name: 'ApprovalPrompt',
  keywords: ['approval', 'permission', 'consent', 'tool', 'gate', 'alert', 'security', 'companion', 'chat'],
  description:
    'The one renderer for "the agent is blocked waiting on your permission". Two surfaces ask that question — the in-chat card (a transcript segment with the chat-scoped trust vocabulary) and the phone companion at #/companion (the GET /api/approvals queue with approve/reject) — and they differ in their DATA and their ACTION VOCABULARY, not in what a permission prompt looks like or how it is announced. So the shell lives here once and each surface supplies an adapter. Announcement contract, identical on both: role=group named for the tool (several prompts can pend at once), plus an inner role=alert so arrival interrupts, because the agent is halted until it is answered.',
  props: [
    { name: 'tool', description: 'The tool awaiting permission. Also the group\'s accessible name ("Permission needed to run <tool>") so a screen-reader user knows which prompt they are in.' },
    { name: 'args', description: "The tool's arguments, raw. `compact` collapses them to a single truncated mono line beside the tool name; `roomy` shows ALL of them in a wrapped, scrollable block — on a phone the arguments ARE the decision, so truncating them would hide what is being consented to." },
    { name: 'purpose', description: "Why the agent wants to run it, when the provider supplied a reason. Rendered as a dimmed line under the arguments." },
    { name: 'badge', description: "Optional chip beside the 'Permission needed' heading — the chat surface passes its risk indicator here. Purely informational; it never gates." },
    { name: 'meta', description: 'Context block under the arguments — the chat passes its blast-radius chips (what the call can touch, established facets only), the companion passes session / requested-by / how-long-waiting. Omit whatever the surrounding surface already supplies (the chat transcript supplies the session).' },
    { name: 'scope', description: 'Optional block between the brief and the action row: how far the answer should reach. The chat passes its remember-scope picker here (it can persist a standing grant); the companion passes nothing, because its queue posts approve/reject only. A slot, not a shared control — the caller owns the wording of every promise it makes about what gets remembered, and nothing here may be auto-selected beyond the narrowest option.' },
    { name: 'choices', description: 'The answers offered, in order, least-privilege first. Each is { key, label, icon, tone?, name?, busy?, onClick }. Pass `name` whenever several prompts can pend at once, so the composed name ("Allow Bash") replaces an ambiguous bare verb.' },
    { name: 'density', description: "'compact' (default) for the chat column: inline margins, a truncated argument line, 28px action pills. 'roomy' for the phone: full arguments, a metadata block, and 44px action targets for a thumb." },
    { name: 'className', description: 'Extra classes on the card shell — for layout only (no raw hex/px; the token-lint ratchet fails the build otherwise).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Render this rather than a new card whenever a surface asks for tool permission. Two renderers for one security concept is exactly the drift this kit exists to prevent — a wording or announcement fix would otherwise land on one surface only.' },
    { guidance: true, description: 'Give every choice a `name` on a QUEUE surface. One card per pending approval means N buttons labelled "Allow", which announce identically in the accessibility tree; the composed name is what distinguishes them.' },
    { guidance: true, description: "Set `busy` on the choices of a prompt whose answer is in flight, so a decision cannot be submitted twice. It is natively disabled and marked aria-busy — the deliberate exception to the soft-off contract, since there is no reason a user could act on." },
    { guidance: false, description: 'Do not put an sr-only span inside a choice to carry its name — it is concatenated into the accessible name alongside the visible verb. Use `name`, which becomes aria-label and replaces it.' },
    { guidance: false, description: 'Do not give an approve choice the `primary` tone on a surface whose job is to make someone weigh a decision. A solid primary fill reads as "this is the action to take", which is advocacy — the chat card passes no tone for Allow for exactly that reason. (The companion still does, and should be revisited with it.)' },
    { guidance: false, description: "Do not truncate `args` before passing them at 'roomy' density. The phone surface deliberately shows the whole payload; the caller trimming it re-introduces the hidden-consent problem the density exists to fix." },
  ],
  anatomy: [
    'motion.div role=group, warn-tinted card (aria-label names the tool)',
    'aria-hidden ShieldQuestion',
    'role=alert "Permission needed" + optional badge',
    "tool name + arguments (truncated mono line at 'compact', wrapped scrollable block at 'roomy')",
    'optional purpose line',
    'optional meta block',
    'optional scope block (the chat\'s remember-scope picker + the promise it makes)',
    'action row of choice pills (danger = the deny edge; a tone is optional and the chat card deliberately passes none for Allow, so the prompt never reads as a recommendation)',
  ],
}

export default doc
