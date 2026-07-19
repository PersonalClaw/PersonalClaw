import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { EditorState, Compartment } from '@codemirror/state'
import { EditorView, keymap, placeholder as cmPlaceholder } from '@codemirror/view'
import { markdown } from '@codemirror/lang-markdown'
import { liveMarkdown, liveMarkdownTheme } from './liveMarkdown'
import { MentionMenu, type MentionPick } from './MentionMenu'
import { SlashMenu } from './SlashMenu'

/** Detect an active `@query` at the caret (word-boundary @, query has no space). */
function activeMention(value: string, caret: number): { query: string; at: number } | null {
  const upto = value.slice(0, caret)
  const m = upto.match(/(?:^|\s)@([^\s@]*)$/)
  if (!m) return null
  return { query: m[1], at: caret - m[1].length - 1 }
}

// A slash command is only meaningful as the FIRST token of the message (matches
// the backend's is_slash = first word ∈ _SLASH_COMMANDS). So the "/"-menu opens
// only when the whole doc so far is `/word` (no space yet) with the caret in it.
function activeSlash(value: string, caret: number): { query: string } | null {
  const m = value.match(/^\/([a-z]*)$/i)
  if (!m) return null
  if (caret < 1 || caret > m[0].length) return null
  return { query: m[1] }
}

export interface MarkdownInputHandle {
  focus: () => void
  /** Replace the current selection (or insert at caret) with `text`. */
  insertAtCaret: (text: string) => void
}

interface Props {
  value: string
  onChange: (v: string) => void
  onSend: () => void
  canSend: boolean
  placeholder?: string
  maxHeight: number
  minHeight: number
  onFocusChange?: (focused: boolean) => void
  onOptimize?: () => void
  history?: string[]
  onMentionFile?: (file: { path: string; name: string }) => void
  /** notified when a knowledge-library item is @-mentioned (host records the id). */
  onMentionKnowledge?: (item: { id: string; name: string }) => void
  mentionProject?: string
  /** enable the "/"-command autocomplete menu (chat only). */
  slashCommands?: boolean
  onLargePaste?: (text: string) => boolean
  /** mobile viewport → Enter inserts a newline instead of sending (send is button-only). */
  mobile?: boolean
}

/** A CodeMirror-6-backed message input with Obsidian-style live markdown: the
 *  line the caret is on shows raw markdown; other lines render. Replaces the
 *  composer's <textarea> while preserving its behaviors — Enter to send,
 *  Shift+Enter newline, ⌘/Ctrl+Enter optimize, ↑/↓ prompt-history at the text
 *  boundaries, @-mention file picker, and large-paste interception. */
export const MarkdownInput = forwardRef<MarkdownInputHandle, Props>(function MarkdownInput({
  value, onChange, onSend, canSend, placeholder, maxHeight, minHeight,
  onFocusChange, onOptimize, history, onMentionFile, onMentionKnowledge, mentionProject, slashCommands, onLargePaste, mobile,
}, ref) {
  const hostRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<EditorView | null>(null)
  // The placeholder lives in its own compartment so it can be reconfigured when the prop
  // changes (e.g. the Loop composer's kind slider) — CodeMirror bakes extensions at editor
  // construction, so without this the placeholder froze at its first-mount value.
  const placeholderComp = useRef(new Compartment())
  // Latest props for the (static) CM extensions to read without rebuilding.
  const cb = useRef({ value, onChange, onSend, canSend, onOptimize, history, onLargePaste, onMentionFile, onMentionKnowledge, slashCommands, mobile })
  cb.current = { value, onChange, onSend, canSend, onOptimize, history, onLargePaste, onMentionFile, onMentionKnowledge, slashCommands, mobile }

  const [mention, setMention] = useState<{ query: string; at: number } | null>(null)
  const [slash, setSlash] = useState<{ query: string } | null>(null)
  const slashRef = useRef(slash); slashRef.current = slash
  const dismissedRef = useRef<string | null>(null)
  const slashDismissedRef = useRef<string | null>(null)
  const histIdx = useRef(-1)
  const draftBeforeHist = useRef('')
  // set while setDoc() applies a recalled history entry, so the updateListener's
  // docChanged handler doesn't reset histIdx (which would pin recall to the most
  // recent entry — ↑ could never walk further back).
  const navigatingHist = useRef(false)

  // recompute the active @-mention from the live caret.
  const syncMention = (view: EditorView) => {
    if (!cb.current.onMentionFile && !cb.current.onMentionKnowledge) return
    const caret = view.state.selection.main.head
    const text = view.state.doc.toString()
    const m = activeMention(text, caret)
    if (m && dismissedRef.current === `${m.at}:${m.query}`) { setMention(null); return }
    dismissedRef.current = null
    setMention(m)
  }

  // recompute the active "/"-command from the live caret (first-token only).
  const syncSlash = (view: EditorView) => {
    if (!cb.current.slashCommands) return
    const caret = view.state.selection.main.head
    const text = view.state.doc.toString()
    const s = activeSlash(text, caret)
    if (s && slashDismissedRef.current === s.query) { setSlash(null); return }
    slashDismissedRef.current = null
    setSlash(s)
  }

  // ── build the editor once ──
  useEffect(() => {
    const updateListener = EditorView.updateListener.of((u) => {
      if (u.docChanged) {
        const text = u.state.doc.toString()
        if (text !== cb.current.value) {
          // user edits reset history navigation; programmatic recall writes don't.
          if (!navigatingHist.current) histIdx.current = -1
          cb.current.onChange(text)
        }
      }
      if (u.docChanged || u.selectionSet) { syncMention(u.view); syncSlash(u.view) }
      if (u.focusChanged) onFocusChange?.(u.view.hasFocus)
    })

    const sendKeys = keymap.of([
      { key: 'Enter', run: (v) => {
          // while the mention or slash menu is open, Enter is owned by it (the
          // menu's capture-phase keydown selects the highlighted item).
          if (mentionRef.current || slashRef.current) return false
          // On mobile, Enter inserts a newline — sending is button-only (a phone
          // keyboard's return key must not fire off a half-typed message). Desktop
          // keeps Enter-to-send / Shift+Enter-newline.
          if (cb.current.mobile) { v.dispatch(v.state.replaceSelection('\n')); return true }
          if (cb.current.canSend) { histIdx.current = -1; cb.current.onSend(); return true }
          return false
        } },
      { key: 'Shift-Enter', run: (v) => { v.dispatch(v.state.replaceSelection('\n')); return true } },
      { key: 'Mod-Enter', run: () => {
          if (cb.current.onOptimize && cb.current.canSend) { cb.current.onOptimize(); return true }
          return false
        } },
      { key: 'ArrowUp', run: (v) => historyNav(v, 'up') },
      { key: 'ArrowDown', run: (v) => historyNav(v, 'down') },
    ])

    const view = new EditorView({
      state: EditorState.create({
        doc: value,
        extensions: [
          markdown(),
          liveMarkdown,
          liveMarkdownTheme,
          EditorView.lineWrapping,
          placeholderComp.current.of(cmPlaceholder(placeholder ?? '')),
          sendKeys,
          updateListener,
          EditorView.domEventHandlers({
            paste: (e) => {
              if (!cb.current.onLargePaste) return false
              const text = e.clipboardData?.getData('text/plain')
              if (text && cb.current.onLargePaste(text)) { e.preventDefault(); return true }
              return false
            },
          }),
        ],
      }),
      parent: hostRef.current!,
    })
    viewRef.current = view
    return () => { view.destroy(); viewRef.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ↑/↓ prompt-history recall — only at the text boundaries (else normal cursor).
  function historyNav(view: EditorView, dir: 'up' | 'down'): boolean {
    const hist = cb.current.history ?? []
    if (!hist.length) return false
    const { head, anchor } = view.state.selection.main
    const len = view.state.doc.length
    const atStart = head === 0 && anchor === 0
    const atEnd = head === len && anchor === len
    const cur = view.state.doc.toString()
    if (dir === 'up' && (histIdx.current !== -1 || (atStart && !cur.trim()))) {
      if (histIdx.current === -1) draftBeforeHist.current = cur
      histIdx.current = histIdx.current === -1 ? hist.length - 1 : Math.max(0, histIdx.current - 1)
      setHistDoc(view, hist[histIdx.current])
      return true
    }
    if (dir === 'down' && histIdx.current !== -1 && atEnd) {
      histIdx.current += 1
      setHistDoc(view, histIdx.current >= hist.length ? (histIdx.current = -1, draftBeforeHist.current) : hist[histIdx.current])
      return true
    }
    return false
  }

  function setDoc(view: EditorView, text: string) {
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text }, selection: { anchor: text.length } })
  }

  // setDoc for prompt-history recall — guards histIdx from the docChanged reset
  // so repeated ↑ can walk further back through history (not pin to the latest).
  function setHistDoc(view: EditorView, text: string) {
    navigatingHist.current = true
    try { setDoc(view, text) } finally { navigatingHist.current = false }
  }

  // keep the editor's doc in sync when the host changes `value` externally
  // (optimize result, history pick from outside, mention insert, clear-on-send).
  useEffect(() => {
    const view = viewRef.current
    if (!view) return
    if (view.state.doc.toString() !== value) setDoc(view, value)
  }, [value])

  // reconfigure the placeholder when it changes (e.g. the kind slider) — the editor is
  // built once, so the placeholder must be swapped via its compartment, not a rebuild.
  useEffect(() => {
    const view = viewRef.current
    if (!view) return
    view.dispatch({ effects: placeholderComp.current.reconfigure(cmPlaceholder(placeholder ?? '')) })
  }, [placeholder])

  // auto-grow: the editor min-height = restH, grows with content up to maxHeight.
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    host.style.minHeight = `${minHeight}px`
    host.style.maxHeight = `${maxHeight}px`
  }, [minHeight, maxHeight])

  const mentionRef = useRef(mention)
  mentionRef.current = mention

  useImperativeHandle(ref, () => ({
    focus: () => viewRef.current?.focus(),
    insertAtCaret: (text: string) => {
      const view = viewRef.current
      if (!view) return
      view.dispatch(view.state.replaceSelection(text))
      requestAnimationFrame(() => view.focus())
    },
  }), [])

  function pickMention(pick: MentionPick) {
    const view = viewRef.current
    if (!view || !mention) return
    const text = view.state.doc.toString()
    const before = text.slice(0, mention.at)
    const after = text.slice(mention.at + 1 + mention.query.length)
    const next = `${before}@${pick.name} ${after}`
    const caret = before.length + pick.name.length + 2
    view.dispatch({ changes: { from: 0, to: text.length, insert: next }, selection: { anchor: caret } })
    // A prompt pick is a plain `@name` reference the backend expands at send — no
    // side-channel meta (unlike file/knowledge, which thread a path/id into meta).
    if (pick.kind === 'file') cb.current.onMentionFile?.({ path: pick.path, name: pick.name })
    else if (pick.kind === 'knowledge') cb.current.onMentionKnowledge?.({ id: pick.id, name: pick.name })
    setMention(null)
    requestAnimationFrame(() => view.focus())
  }

  // choose a slash command — replace the doc with `/cmd ` (trailing space so the
  // menu closes and the caret sits ready for args), then refocus.
  function pickSlash(command: string) {
    const view = viewRef.current
    if (!view) return
    const next = `${command} `
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: next }, selection: { anchor: next.length } })
    setSlash(null)
    requestAnimationFrame(() => view.focus())
  }

  return (
    <div className="relative w-full">
      <div ref={hostRef} className="w-full overflow-y-auto px-s pt-1" aria-label="Message input" />
      {(onMentionFile || onMentionKnowledge) && (
        <MentionMenu query={mention?.query ?? ''} anchorRef={hostRef} open={!!mention} project={mentionProject}
          leading={mention?.at === 0}
          onSelect={pickMention}
          onClose={() => { if (mention) dismissedRef.current = `${mention.at}:${mention.query}`; setMention(null) }} />
      )}
      {slashCommands && (
        <SlashMenu query={slash?.query ?? ''} anchorRef={hostRef} open={!!slash}
          onSelect={pickSlash}
          onClose={() => { if (slash) slashDismissedRef.current = slash.query; setSlash(null) }} />
      )}
    </div>
  )
})
