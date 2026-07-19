import type { ReactNode } from 'react'
import type { AgentDef, AgentProvider, DiscoveredAgent, ModelItem, ApprovalMode, TaskMode, ReasoningEffort } from '../../lib/api'

/** Which controls a composer instance surfaces. Chat enables most; the goal
 *  composer enables only the shared core (send + optimize) and supplies its own
 *  chip-form for goal params. This is the single configurability seam. */
export interface ComposerControls {
  agent?: boolean
  model?: boolean
  approval?: boolean
  reasoning?: boolean      // only meaningful if the bound provider supports it
  attach?: boolean
  mic?: boolean
  optimize?: boolean
  slash?: boolean          // "/"-command autocomplete in the input (chat only)
}

export interface ComposerValue {
  agent: string            // bound agent label/id
  model: string
  approval: ApprovalMode
  taskMode: TaskMode       // orthogonal to approval — gates which tools + framing
  reasoning: ReasoningEffort
}

export interface ComposerData {
  agents: AgentDef[]
  providers: AgentProvider[]
  discovered: Record<string, DiscoveredAgent[]>
  models: ModelItem[]
}

export interface ComposerProps {
  value: string
  onChange: (v: string) => void
  onSend: () => void
  streaming?: boolean
  /** Send button shows a circular spinner (no stop/send) — for a one-shot
   *  pre-send processing step like the goal composer's analyze pass. */
  processing?: boolean
  onStop?: () => void
  placeholder?: string
  controls?: ComposerControls
  data?: ComposerData
  /** current selections + setters (wired to the session by the host page) */
  selection?: ComposerValue
  onSelect?: (patch: Partial<ComposerValue>) => void
  onAttach?: (files: File[]) => void
  /** Open the saved-prompt palette. When set, the "+" toolbar menu offers "Saved
   *  prompts" — replacing the old floating chip that overlapped the composer edge. */
  onOpenPrompts?: () => void
  /** Extra items rendered at the bottom of the "+" menu (host-owned, session-scoped
   *  affordances like Auto-nudge). Receives `close` to dismiss the menu on action. */
  plusMenuExtra?: (close: () => void) => ReactNode
  onFocusChange?: (focused: boolean) => void
  /** workspace dir to scope the @-mention file search (optional) */
  mentionProject?: string
  /** notified when a file is picked via @-mention (host records the path for send) */
  onMentionFile?: (file: { path: string; name: string }) => void
  /** notified when a knowledge item is picked via @-mention (host records the id) */
  onMentionKnowledge?: (item: { id: string; name: string }) => void
  /** large-paste handler — host collapses it to an attachment + inline marker.
   *  Return true if the paste was consumed (Composer then suppresses default). */
  onLargePaste?: (text: string) => boolean
  /** Optimize the current draft via the prompt optimizer; host swaps `value`. */
  onOptimize?: () => void
  /** True while an optimize round-trip is in flight (spinner + disabled). */
  optimizing?: boolean
  /** Prior user messages, oldest→newest, for ↑/↓ history recall in an empty draft. */
  history?: string[]
  /** Transcribe a recorded audio blob to text (host inserts it into the draft). */
  onTranscribe?: (blob: Blob) => Promise<string>
  /** Report a voice-input failure (mic blocked / no STT) so the host can surface it. */
  onMicError?: (msg: string) => void
  /** When true, the send button becomes a "queue" affordance — the host runs the
   *  message after the in-flight turn finishes instead of dropping it. */
  canQueue?: boolean
  /** 0–100 context-window usage for the bound session → ring on the model pill. */
  contextPct?: number
  /** Minimum trimmed length before Send enables (default 1). The goal composer
   *  needs ≥20 chars to plan, so the button stays disabled (not a silent no-op)
   *  until the draft is long enough. */
  minChars?: number
  /** Monotonic counters — incrementing opens the model / agent / reasoning pill
   *  popover. Drives the "/model", "/agent" and "/effort" GUI-affordance slash commands. */
  openModelSignal?: number
  openAgentSignal?: number
  openReasoningSignal?: number
}
