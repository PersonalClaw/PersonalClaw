import type { UiDoc } from './uiDoc'

// Doc object for Composer — the one configurable chat/prompt input. Authored:
// keywords, prose, per-prop descriptions, Do/Don't, anatomy. Prop type/required
// are DERIVED from Composer.tsx (ComposerProps) at build time — never restate them.
const doc: UiDoc = {
  name: 'Composer',
  keywords: ['composer', 'input', 'chat', 'prompt', 'editor', 'send', 'mic', 'attach', 'markdown', 'textarea'],
  description:
    'The one configurable message composer — used by Chat and the goal composer. A surface sheet wrapping a live-markdown editor (CodeMirror, not a <textarea>) that auto-grows to a user-resizable max, an inline pill cluster ([+] · agent · model · approval · reasoning), and a right-hand action cluster (optimize · mic · send/stop/steer/queue). The `controls` prop is the single seam that picks which pills and "+"-menu items appear; drag-and-drop file attach, focus/drag-over motion, and ↑/↓ history recall come built in.',
  props: [
    { name: 'value', description: 'The current draft text (controlled).' },
    { name: 'onChange', description: 'Fires with the new draft on every edit.' },
    { name: 'onSend', description: 'Called to send the draft — also invoked as "steer" mid-stream and "queue" when canQueue.' },
    { name: 'streaming', description: 'True while a turn is streaming — flips the send button to Stop (or Steer with a typed draft).' },
    { name: 'processing', description: 'One-shot pre-send processing (e.g. the goal analyze pass) → a spinning send button with no stop/queue affordance.' },
    { name: 'onStop', description: 'Called from the Stop button to abort the running turn.' },
    { name: 'placeholder', description: "Editor placeholder (default 'Ask anything')." },
    { name: 'controls', description: 'The single configurability seam — booleans picking which pills + "+"-menu items surface (agent/model/approval/reasoning/attach/mic/optimize/slash). Chat enables most; the goal composer enables only send+optimize.' },
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
    { name: 'onTranscribe', description: 'Transcribes a recorded audio blob to text — required (with controls.mic) to enable voice input.' },
    { name: 'onMicError', description: 'Reports a voice-input failure (mic blocked / no STT) so the host can surface it.' },
    { name: 'handsFree', description: 'Hands-free voice loop config (phrase lists + mute-while-speaking) — with onHandsFreeSubmit it adds the hands-free toggle beside the mic.' },
    { name: 'onHandsFreeSubmit', description: 'Receives the accumulated dictation once a confirmation phrase fires the turn; required for the hands-free toggle to appear.' },
    { name: 'screenShare', description: 'Screen-context sharing state + toggle (MULTIMODAL-IO §5.2), owned by the HOST because the header sharing chip must outlive the composer\u2019s scroll. available:false (the OFF-by-default config flag) renders no control at all; a non-empty disabledReason renders it disabled carrying that reason.' },
    { name: 'naturalVoice', description: 'Natural voice (PT-7) \u2014 the per-conversation plainer-prose scope. `effective`/`source` arrive already RESOLVED by the backend, which owns the single statement of the resolution order (per-conversation over the agent default); the pill displays them and never re-derives it. Absent \u2192 no control, which is what the goal composer (no conversation) gets.' },
    { name: 'canQueue', description: 'When true, the send button becomes a "queue" affordance — the host runs the message after the in-flight turn instead of dropping it.' },
    { name: 'contextPct', description: '0–100 context-window usage for the bound session → ring on the model pill.' },
    { name: 'minChars', description: 'Minimum trimmed length before Send enables (default 1); the goal composer sets ≥20 so the button stays visibly disabled, not a silent no-op.' },
    { name: 'openModelSignal', description: 'Monotonic counter — incrementing opens the model pill popover (drives the "/model" GUI slash command).' },
    { name: 'openAgentSignal', description: 'Monotonic counter — incrementing opens the agent pill popover (drives the "/agent" GUI slash command).' },
    { name: 'openReasoningSignal', description: 'Monotonic counter — incrementing opens the reasoning pill popover (drives the "/effort" GUI slash command).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for Composer for any chat/prompt input rather than hand-rolling a <textarea> — the send/stop/steer/queue state machine, mic-to-text, resize, @-mentions, history recall, and drag-drop attach all come built in.' },
    { guidance: true, description: 'Use `controls` as the single configurability seam — enable only the pills a surface needs (Chat enables most; the goal composer just send + optimize) rather than forking the component.' },
    { guidance: true, description: 'Feed the pills real options via `data` + `selection`/`onSelect` so they reflect the bound session; pass `contextPct` to show context-window pressure on the model pill.' },
    { guidance: true, description: 'Set `minChars` when a longer draft is required so Send reads as visibly disabled rather than silently no-opping (the goal composer needs ≥20 to plan).' },
    { guidance: false, description: 'Do not enable controls.attach or controls.mic without also passing onAttach / onTranscribe — the affordance only appears when its handler exists.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens (the token-lint ratchet fails the build otherwise).' },
  ],
  anatomy: ['outer motion.div (focus / drag-over lift + scale spring)', 'conic-gradient ring glow (surges + breathes on drag-over)', 'surface sheet (pill↔sheet radius morph)', 'drag-and-drop attach overlay (AnimatePresence)', 'top-center resize handle grip', 'MarkdownInput (CodeMirror live-markdown editor)', 'pill cluster row ([+] menu · agent · model · approval · reasoning)', 'action cluster (optimize · mic · send/stop/steer/queue)', 'hidden file input'],
}

export default doc
