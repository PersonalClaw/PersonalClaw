import { useEffect, useLayoutEffect, useRef, type ReactNode, type RefObject } from 'react'
import { PanelBottomOpen } from 'lucide-react'
import { Button } from '../../../ui/Button'
import { bindingLabel, heldWithAModifier } from '../../../app/keymap'

// ── Reading mode's fold ───────────────────────────────────────────────────────────────────────
//
// While the owner reads a long answer, the composer folds away and the transcript takes the column's
// whole height (`web/REDESIGN.md` §5 convention 11). The composer stays mounted while folded, so its
// draft, attachments and editor are all still there when it comes back, which is on:
//
// - the key, or the line it folds to (a click, or Enter on it);
// - focus: anything that puts focus in the composer (Tab, `/`, a follow-up chip, a quote) opens it;
// - typing: a printable key pressed outside a field opens it, and lands at the end of the draft.
//
// Folded, the composer is clipped to nothing and transparent, but kept in the tab order, so focus can
// still reach it and opening it is what focus does. The line it folds to floats over the end of the
// transcript, as the return-to-newest control does, and says what it opens.

/** How the folded composer is held: no height, nothing painted, nothing to click, still focusable. */
const FOLDED = { height: 0, overflow: 'clip', opacity: 0, pointerEvents: 'none' } as const

/** The room the folded line takes over the end of the transcript, so the newest line of an answer
 *  ends above it. */
export const FOLD_ROOM = '3rem'

/** The longest stretch of a draft the folded line shows. */
const DRAFT_PREVIEW = 48

/** Where a key is a field's own, or an open layer's, so typing there never opens the composer. */
const KEEPS_ITS_KEYS = 'input, textarea, select, [contenteditable=true], [role=dialog], [role=menu], [role=listbox]'

function editorOf(composerRef: RefObject<HTMLElement | null>): HTMLElement | null {
  return composerRef.current?.querySelector<HTMLElement>('.cm-content') ?? null
}

export function ComposerFold({ folded, onUnfold, composerRef, draft, onType, children }: {
  folded: boolean
  onUnfold: () => void
  composerRef: RefObject<HTMLElement | null>
  draft: string
  /** Puts a key typed while folded at the end of the draft. */
  onType: (key: string) => void
  children: ReactNode
}) {
  const regionRef = useRef<HTMLDivElement>(null)
  const lineRef = useRef<HTMLSpanElement>(null)
  const typed = useRef(onType)
  typed.current = onType

  // Folding from inside the composer (its key pressed while typing) would leave focus on something
  // no one can see, so it moves to the line that brings the composer back.
  useLayoutEffect(() => {
    if (folded && regionRef.current?.contains(document.activeElement)) lineRef.current?.querySelector('button')?.focus()
  }, [folded])

  // Typing outside a field opens it, and the key typed is the draft's next character. `/` is left to
  // the composer's own shortcut, which puts focus in it, and that opens it too.
  useEffect(() => {
    if (!folded) return
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing || heldWithAModifier(e)) return
      if (e.key.length !== 1 || e.key === '/') return
      const target = e.target instanceof HTMLElement ? e.target : null
      if (target?.closest(KEEPS_ITS_KEYS)) return
      // Space activates a control that has focus; it types only from the page itself.
      if (e.key === ' ' && target && target !== document.body) return
      e.preventDefault()
      typed.current(e.key)
      editorOf(composerRef)?.focus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [folded, composerRef])

  const text = draft.trim()
  const preview = text.length > DRAFT_PREVIEW ? `${text.slice(0, DRAFT_PREVIEW).trimEnd()}…` : text
  return (
    <>
      {folded && (
        <span ref={lineRef} className="pointer-events-auto mx-auto flex justify-center" style={{ maxWidth: 'var(--content-width)' }}>
          <Button variant="secondary" size="sm" onClick={() => editorOf(composerRef)?.focus()}
            title={`Unfold the composer (${bindingLabel('reading-mode')})`}
            className="max-w-full shadow-md">
            <PanelBottomOpen size={14} aria-hidden className="shrink-0 text-on-surface-low" />
            <span className="truncate text-on-surface-var">{preview ? `Continue your draft: ${preview}` : 'Write a reply'}</span>
            <kbd aria-hidden className="rounded-md bg-surface-highest px-1.5 py-0.5 font-mono text-on-surface-var text-[0.75rem]">{bindingLabel('reading-mode')}</kbd>
          </Button>
        </span>
      )}
      <div ref={regionRef} data-composer-region data-folded={folded}
        onFocusCapture={folded ? onUnfold : undefined}
        className="mx-auto flex flex-col items-center"
        style={folded ? { ...FOLDED, maxWidth: 'var(--content-width)' } : { maxWidth: 'var(--content-width)' }}>
        {children}
      </div>
    </>
  )
}
