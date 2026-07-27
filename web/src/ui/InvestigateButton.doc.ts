import type { UiDoc } from './uiDoc'

// Doc object for InvestigateButton — the one shared "chat about this" affordance
// (INVESTIGATE-ANYWHERE plan 60). Every entity row uses THIS; no bespoke variants.
const doc: UiDoc = {
  name: 'InvestigateButton',
  keywords: ['investigate', 'chat', 'context', 'entity', 'ask', 'question', 'inspect'],
  description:
    "An icon button (MessageCircleQuestion, tooltip 'Investigate in chat') that opens a chat pre-loaded with one entity's full context. The context envelope is composed SERVER-side by the entity kind's registered resolver (a client can't forge a snapshot), fenced as untrusted data at injection, and the session opens in read-only `ask` task mode — investigating never mutates the entity; the user escalates the mode themselves. The one shared primitive: a surface passes only {kind, id}.",
  props: [
    { name: 'kind', type: 'string', required: true, description: "The entity kind — a key in the backend investigate resolver registry (inbox_item | loop_finding | … — a new kind registers a resolver first)." },
    { name: 'id', type: 'string', required: true, description: 'The entity id within the owning store (e.g. an inbox item id, "loopid:cycle").' },
    { name: 'backLink', type: 'string', required: false, description: "Hash route back to the source surface; overrides the resolver's default (the chat header chip deep-links here)." },
    { name: 'size', type: 'number', required: false, description: 'Hit-area size in px (default 24 — dense row chrome).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Drop it on entity ROWS and detail headers — anywhere a user might ask "what is this / why did this happen". Failure rows (cron/loop/subagent failures, Doctor findings) benefit most.' },
    { guidance: true, description: 'Pass a backLink that lands on the EXACT source row/tab when the surface has deep-link params — the chip is the "current state" escape hatch for snapshot staleness.' },
    { guidance: false, description: 'Do not build a bespoke "chat about this" flow (composer seeding with entity text, custom launchChat prompts) — unfenced entity text in the visible message is the exact gap this primitive closes.' },
    { guidance: false, description: "Do not suggest `agent` mode for failure kinds ('fix this crash') — the doctrine is ask-by-default; the USER escalates, not the button." },
  ],
  anatomy: ['IconButton (MessageCircleQuestion glyph, busy-disabled during the POST)'],
}

export default doc
