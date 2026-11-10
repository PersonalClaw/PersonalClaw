import { useEffect, useId, useLayoutEffect, useRef, type ReactNode, type Ref } from 'react'
import { Settings, Sparkles } from 'lucide-react'
import explanationsTable from '../../../../../src/personalclaw/explanations.json'
import { Button } from '../../../ui/Button'
import { ChoiceList, type Choice } from '../../../ui/ChoiceList'
import { IconButton } from '../../../ui/IconButton'
import { Modal } from '../../../ui/Modal'
import { ReplyStyleChoices, ShowWhatIsSent } from '../../../ui/composer/ReplyStyleChoices'
import { SavedToast, useSavedFlash } from '../../settings/settingsUI'
import type { ExplainChoiceState } from './useExplainChoice'
import type { PickResult, ReplyStylePillState } from './useReplyStyle'

// ── Chat preferences: the rare overrides, one gear on the chat box ──────────────────────────────
//
// This chat's explanations, its reply style and Optimize sit behind one gear, so the footer keeps
// what is picked often (who answers, the task mode, where the next turn may act). A choice here
// applies to this chat only: your defaults and the agent stay as they are. Both choices are the
// backend's resolution (`useExplainChoice`, `useReplyStyle`); nothing here works one out.

const EXPLANATIONS = explanationsTable as { id: string; label: string; hint: string }[]

/** An explanation id as its row names it. */
const explanationName = (id: string) => EXPLANATIONS.find((e) => e.id === id)?.label ?? id

/** Who chose the explanation, as "Now:" says it: the scopes of `explanations.EXPLAIN_PRECEDENCE`. */
const EXPLAIN_CHOSEN_BY: Record<string, string> = { conversation: 'for this chat', platform: 'your default' }

/** What this chat states for itself, as the gear's description names it. */
export function changedHere(style: Pick<ReplyStylePillState, 'choice'>, explain: Pick<ExplainChoiceState, 'choice'>): string[] {
  const changed: string[] = []
  if (explain.choice !== '') changed.push('explanations')
  if (style.choice !== '') changed.push('reply style')
  return changed
}

/** The gear on the chat box. A 6 px dot sits on it while this chat states its own reply style or
 *  explanations; the dot is decoration, and its meaning is in the description. A style or an
 *  explanation that comes from the agent or your defaults is named inside, not dotted. */
export function ChatPreferencesGear({ open, onOpen, changed, wrapRef }: {
  open: boolean
  onOpen: () => void
  /** What this chat states for itself (`changedHere`). */
  changed: string[]
  /** The gear's own box, so the window can give the focus back to its button. */
  wrapRef?: Ref<HTMLSpanElement>
}) {
  const said = changed.length > 0 ? ` Changed for this chat: ${changed.join(', ')}.` : ''
  return (
    <span ref={wrapRef} className="relative inline-flex shrink-0">
      <IconButton icon={Settings} label="Chat preferences" expanded={open} size={36} iconSize={18} onClick={onOpen}
        tip={`Chat preferences: explanations, reply style and Optimize, for this chat.${said}`} />
      {changed.length > 0 && (
        <span aria-hidden data-prefs-changed className="pointer-events-none absolute right-1.5 top-1.5 size-1.5 rounded-pill bg-primary" />
      )}
    </span>
  )
}

/** The Explanations section: your default, then the table's rows. "Now:" is the server's resolved
 *  choice and its scope; before the chat exists, the pick. */
function ExplanationsChoices({ choice, effective, source, homeDefault, refused, onSelect, status, checkedRef }: {
  choice: string
  /** The server's sentence for the latest pick it refused, said under the choices. */
  refused?: string
  effective: string
  source: string
  homeDefault: string
  onSelect: (explain: string) => void
  status: ReactNode
  checkedRef: Ref<HTMLButtonElement>
}) {
  const headingId = useId()
  const yours = homeDefault ? `Your default (${explanationName(homeDefault)})` : 'Your default'
  const now = source === ''
    ? (choice ? explanationName(choice) : yours)
    : `${explanationName(effective)}, ${EXPLAIN_CHOSEN_BY[source] ?? source}`
  const options: Choice[] = [
    { key: '', label: yours, hint: 'Follows your default, and any change to it.' },
    ...EXPLANATIONS.map((e) => ({ key: e.id, label: e.label, hint: e.hint })),
  ]
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-s">
      <div className="flex flex-wrap items-baseline justify-between gap-x-s">
        <h2 id={headingId} data-type="title-s" className="text-on-surface">Explanations</h2>
        <span className="relative flex items-center gap-s">
          {status}
          <span data-type="caption" className="text-on-surface-low">Now: {now}</span>
        </span>
      </div>
      <ChoiceList labelledBy={headingId} options={options} value={choice} onChange={onSelect} checkedRef={checkedRef} />
      {refused && <p role="alert" data-type="caption" className="text-on-surface-low">Not saved: {refused}</p>}
    </section>
  )
}

/** The Chat preferences window: Explanations, Reply style, what is sent, and Optimize this draft.
 *
 *  Focus opens on the checked Explanations row, set in a layout effect, which runs before the
 *  Modal's trap (a passive effect) and so is left where it is. A pick applies at once and says
 *  "Saved" for its section; a refused one is told and its row goes back. Escape closes the window
 *  and leaves a running turn alone: the turn's Stop key yields while a modal is open. A refused pick
 *  says the server's sentence in its section, as an alert, and in a toast, which
 *  outlasts a window closed before the answer lands. */
export function ChatPreferencesWindow({ replyStyle, explain, readHomeDefault, canOptimize, optimizing = false, onOptimize, onChangeDefaults, onClose }: {
  replyStyle: ReplyStylePillState
  explain: ExplainChoiceState
  /** Reads your default for a chat not created yet (no session detail carries it then). */
  readHomeDefault: () => void
  /** The draft meets the send minimum. */
  canOptimize: boolean
  /** A rewrite is under way: the button says so and takes no second press. */
  optimizing?: boolean
  onOptimize: () => void
  onChangeDefaults: () => void
  onClose: () => void
}) {
  const leadId = useId()
  const draftId = useId()
  const checked = useRef<HTMLButtonElement>(null)
  const [explainSaved, flashExplain] = useSavedFlash()
  const [styleSaved, flashStyle] = useSavedFlash()
  useLayoutEffect(() => { checked.current?.focus() }, [])
  // Read once as it opens: `explain.source` is '' only before the chat exists.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (explain.source === '' && !explain.homeDefault) readHomeDefault() }, [])
  const saved = (flash: () => void) => (r: PickResult) => { if (r === 'saved') flash() }
  return (
    <Modal title="Chat preferences" size="compact" describedBy={leadId} onClose={onClose}>
      <div className="flex flex-col gap-l">
        <div className="flex flex-wrap items-start justify-between gap-s">
          <p id={leadId} data-type="body-s" className="min-w-0 flex-1 text-on-surface-var">
            For this chat only. Your defaults and the agent stay as they are.
          </p>
          <Button variant="ghost-accent" size="xs" onClick={onChangeDefaults}>Change your defaults</Button>
        </div>
        <ExplanationsChoices {...explain} checkedRef={checked}
          onSelect={(e) => { void explain.onSelect(e).then(saved(flashExplain)) }}
          status={<SavedToast show={explainSaved} />} />
        <ReplyStyleChoices {...replyStyle}
          onSelect={(s) => { void replyStyle.onSelect(s).then(saved(flashStyle)) }}
          status={<SavedToast show={styleSaved} />} />
        <ShowWhatIsSent instruction={replyStyle.preview?.instruction ?? ''} />
        <section aria-labelledby={draftId} className="flex flex-col items-start gap-s">
          <h2 id={draftId} data-type="title-s" className="text-on-surface">Your draft</h2>
          <Button variant="secondary" size="sm" className="gap-s" onClick={onOptimize} loading={optimizing}
            disabled={!optimizing && !canOptimize} disabledReason="Type something first">
            <Sparkles size={14} aria-hidden /> Optimize this draft{' '}
            <kbd className="rounded-md bg-surface-highest px-1.5 py-0.5 font-mono text-on-surface-var text-[0.75rem]">⌘↵</kbd>
          </Button>
          <p data-type="caption" className="text-on-surface-low">
            Rewrites your draft as a clearer prompt, using this chat for context. You can revert it.
          </p>
        </section>
      </div>
    </Modal>
  )
}
