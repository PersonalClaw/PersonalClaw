import type { UiDoc } from './uiDoc'

// Doc object for ComposerStage — the shared-layout wrapper that flies the Composer
// between the new-chat hero and the bottom dock. Its prop set is ComposerProps
// (forwarded straight through), so it mirrors Composer's props; type/required are
// DERIVED from the source at build time — never restate them here.
const doc: UiDoc = {
  name: 'ComposerStage',
  keywords: ['composer', 'stage', 'layout', 'shared-element', 'morph', 'dock', 'hero', 'motion', 'glow', 'wrapper'],
  description:
    'The Composer wrapped as a single persistent, shared-layout element (`layoutId="composer-stage"`). It forwards a ref to its outer box so a full-bleed DotGlow can measure it live each frame and cast a synced glow from its edges, and passes every Composer prop straight through. Its signature motion: the composer FLIES from the screen-centered new-chat hero down into the bottom dock when a chat starts, settling with an expressiveness-scaled spring. Use it wherever the composer must animate between two page positions rather than sit in one place.',
  props: [
    { name: 'value', description: 'The current draft text (controlled) — forwarded to Composer.' },
    { name: 'onChange', description: 'Fires with the new draft on every edit — forwarded to Composer.' },
    { name: 'onSend', description: 'Called to send the draft — also invoked mid-stream, as "steer" when canSteer and "queue" otherwise, when canQueue.' },
    { name: 'streaming', description: 'True while a turn is streaming — flips the send button to Stop (or Steer with a typed draft).' },
    { name: 'processing', description: 'One-shot pre-send processing (e.g. the goal analyze pass) → a spinning send button with no stop/queue affordance.' },
    { name: 'onStop', description: 'Called from the Stop button to abort the running turn.' },
    { name: 'placeholder', description: "Editor placeholder (default 'Ask anything')." },
    { name: 'controls', description: 'The single configurability seam — booleans picking which pills + "+"-menu items surface (agent/model/approval/reasoning/attach/mic/optimize/slash).' },
    { name: 'data', description: 'Real agent/model/provider option sets feeding the agent + model pills.' },
    { name: 'selection', description: 'Current selections (agent/model/approval/reasoning) the pills reflect — wired to the session by the host.' },
    { name: 'onSelect', description: 'Fires with a partial selection patch when a pill changes agent/model/approval/reasoning.' },
    { name: 'onAttach', description: 'Receives picked/dropped files — required (with controls.attach) to enable the "+" attach button and drag-and-drop.' },
    { name: 'onOpenPrompts', description: 'Opens the saved-prompt palette; when set, the "+" menu offers "Saved prompts".' },
    { name: 'plusMenuExtra', description: 'Host-owned extra items at the bottom of the "+" menu (e.g. Auto-nudge); receives `close` to dismiss the menu on action.' },
    { name: 'onFocusChange', description: 'Notified when the editor gains/loses focus (drives the host layout + focus lift).' },
    { name: 'mentionProject', description: 'Workspace dir scoping the @-mention file search.' },
    { name: 'onMentionFile', description: 'Notified when a file is picked via @-mention (host records the path for send).' },
    { name: 'onMentionKnowledge', description: 'Notified when a knowledge item is picked via @-mention (host records the id).' },
    { name: 'onLargePaste', description: 'Large-paste handler — host collapses it to an attachment + inline marker; return true to consume the paste.' },
    { name: 'onOptimize', description: 'Optimizes the current draft via the prompt optimizer (host swaps `value`); also bound to ⌘↵.' },
    { name: 'optimizing', description: 'True while an optimize round-trip is in flight → spinner + disabled optimize button.' },
    { name: 'history', description: 'Prior user messages oldest→newest, for ↑/↓ history recall in an empty draft.' },
    { name: 'onRecall', description: 'Called as ↑/↓ brings back history[index]; returns the text to show for it. A host that keeps the blocks pasted into a message restores them here, renumbering markers that would meet a block the draft holds.' },
    { name: 'onTranscribe', description: 'Transcribes a recorded audio blob to text — required (with controls.mic) to enable voice input.' },
    { name: 'onMicError', description: 'Reports a voice-input failure (mic blocked / no STT) so the host can surface it.' },
    { name: 'handsFree', description: 'Hands-free voice loop config (phrase lists + mute-while-speaking) — with onHandsFreeSubmit it adds the hands-free toggle beside the mic.' },
    { name: 'onHandsFreeSubmit', description: 'Receives the accumulated dictation once a confirmation phrase fires the turn; required for the hands-free toggle to appear.' },
    { name: 'screenShare', description: 'Screen-context sharing state + toggle, owned by the HOST because the header sharing chip must outlive the composer\u2019s scroll. available:false (the OFF-by-default config flag) renders no control at all; a non-empty disabledReason renders it disabled carrying that reason.' },
    { name: 'naturalVoice', description: 'Natural voice \u2014 the per-conversation plainer-prose scope. `effective`/`source` arrive already RESOLVED by the backend, which owns the single statement of the resolution order (per-conversation over the agent default); the pill displays them and never re-derives it. Absent \u2192 no control, which is what the goal composer (no conversation) gets.' },
    { name: 'canQueue', description: 'When true, a draft can be sent while a turn runs instead of being dropped.' },
    { name: 'canSteer', description: 'Whether the running turn takes a draft in: true shows "Steer — send into the running turn", false shows "Queue — sent when this turn ends". Follows what the host says of the turn it shows.' },
    { name: 'sendHeldReason', description: 'Why a ready draft cannot be sent yet — a file it carries is still uploading. Non-empty → the Send button is off and carries it as its reason; Enter still hands the draft to onSend so the host refuses it with this sentence where the user is looking. A steer into a running turn is not held.' },
    { name: 'hostPills', description: 'Pills the host owns, drawn in the toolbar after the built-in ones — a choice that belongs to what the host creates rather than to a conversation (the loop composer\'s "Runs on").' },
    { name: 'contextPct', description: '0–100 context-window usage for the bound session → ring on the model pill. Omit it (undefined) when the backend reported no measurement — the pill shows a plain dot whose title says why (see `contextWindow`), never a fabricated 0%. 100 means the context is full (including a provider that silently truncated the prompt); the value never exceeds 100 and never falls as the prompt grows.' },
    { name: 'contextWindow', description: 'The window (tokens) the gateway said the bound model is served with, alongside its last reading: `null` = no window is declared or served (the dot then names the Settings field that declares one), `undefined` = nothing said yet. Only read while `contextPct` is undefined, to say why there is no ring.' },
    { name: 'servedModel', description: 'The model the bound chat\'s latest turn was answered by and how it was chosen (`{ model, chosen }`, e.g. "from your Code & tools chain"), as the turn\'s record names it. On Auto the model pill names it ("Auto · <model>"), with the whole sentence in its title. Omit it before any turn has named a model.' },
    { name: 'autoChain', description: 'The Settings → Models chain the bound chat\'s turns run on while the pill is on Auto ("Code & tools" for a chat working in a folder of its own, else "Chat"), which the Auto row names. Omit it when the host does not know; an agent CLI\'s Auto names no chain.' },
    { name: 'minChars', description: 'Minimum trimmed length before Send enables (default 1); a surface needing a longer draft raises it so Send reads as disabled, not a silent no-op.' },
    { name: 'openModelSignal', description: 'Monotonic counter — incrementing opens the model pill popover (drives the "/model" GUI slash command).' },
    { name: 'openAgentSignal', description: 'Monotonic counter — incrementing opens the agent pill popover (drives the "/agent" GUI slash command).' },
    { name: 'openReasoningSignal', description: 'Monotonic counter — incrementing opens the reasoning pill popover (drives the "/effort" GUI slash command).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Use ComposerStage (not a bare Composer) when the composer must morph between two page positions — the shared layoutId is what flies it from the new-chat hero into the bottom dock.' },
    { guidance: true, description: 'Render exactly ONE ComposerStage across the positions it morphs between — the single shared layoutId is what makes the flight continuous rather than a cross-fade of two instances.' },
    { guidance: true, description: 'Forward the ref through to a DotGlow so the glow can measure the live box each frame and stay edge-synced to the composer.' },
    { guidance: false, description: 'Do not reach for ComposerStage when the composer sits in one fixed place — use Composer directly and skip the shared-layout machinery.' },
  ],
  anatomy: ['motion.div (layoutId="composer-stage", expressiveness-scaled settle spring, ref-forwarded outer box)', 'Composer (all props passed through)'],
}

export default doc
