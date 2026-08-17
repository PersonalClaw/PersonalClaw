/** Artifact iteration — the parent half of EDITMODE + annotate mode
 *  (AMBIENT-SURFACES §3 + §4).
 *
 *  Iterating on a visual artifact used to cost a chat turn per tweak. This hook is
 *  the two cheap paths that replace most of them:
 *
 *   · **EDITMODE** — a drag on a colour or range control lands in the frame's `:root`
 *     custom properties over the reserved `__edit_mode_*` channel. There is NO
 *     network call and NO model call in that path, by construction: nothing here can
 *     reach `api`. Drags are COALESCED to one message per animation frame, so a
 *     slider dragged across its range costs one postMessage, not sixty.
 *   · **Save** asks the child what it actually holds (`__edit_mode_read_keys`) and
 *     rewrites the marker-fenced block from THAT answer. The rail's own state is what
 *     it believes it sent; the document is the truth, and a Save that cannot get the
 *     truth refuses rather than writing a guess.
 *   · **Annotate** accumulates clicked-element anchors, each with the user's note,
 *     and composes ONE correction directive — dispatched to whoever owns the artifact
 *     (a chat turn through the widget bridge's C32 refresh-injection path by default,
 *     or a host-supplied target such as a design loop's guidance channel).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  EDIT_MODE_ANNOTATE,
  EDIT_MODE_READ_KEYS,
  EDIT_MODE_SET_KEYS,
  parseEditModeBlock,
  rewriteEditModeBlock,
  type EditModeParam,
} from './editMode'
import { sanitizeCssValue } from './cssSanitize'
import { composeCorrectionDirective, MAX_ANNOTATIONS, type WidgetAnnotation } from './annotate'
import { finishActionText, publishWidgetAction, type WidgetWireHandlers } from './useWidgetActionBridge'

/** How long Save waits for the child's read-back before giving up. A sandboxed
 *  frame that never answers is broken, not slow; writing the rail's own values
 *  instead would silently persist something the user never saw. */
const READ_BACK_TIMEOUT_MS = 2000

/** What a host offers for iterating on the content it is showing. Every field is
 *  optional — a host that supplies neither gets no rail at all. */
export interface IterationTarget {
  /** The saved artifact this content IS. Names the C32 refresh target so a
   *  correction says "refresh THIS view in place" rather than spawning a new one. */
  slug?: string
  /** Persist a renderer-authored source rewrite as a NEW version. Omit and the
   *  EDITMODE rail is live-preview only (an historical version, a read-only host). */
  persistVersion?: (next: string) => void | Promise<void>
  /** Where a correction directive goes. Defaults to the widget action bridge — the
   *  C32 refresh-injection path into chat. A design loop passes its steer channel so
   *  the directive lands in the loop's guidance instead. */
  correction?: (directive: string) => void | Promise<void>
}

export interface ArtifactIteration {
  /** Declared tunables, or [] when the artifact declares none. */
  params: EditModeParam[]
  /** Descriptors the artifact declared that were refused or past the cap. */
  droppedParams: number
  /** Live rail values (what we have SENT, not what the document holds). */
  values: Record<string, string>
  /** Move one param. Coalesced — see the batching note above. */
  setValue: (key: string, value: string) => void
  /** Any param moved since the last successful save. */
  dirty: boolean
  save: () => Promise<void>
  saving: boolean
  /** Whether the artifact can be persisted at all (a host supplied persistVersion). */
  savable: boolean
  annotating: boolean
  toggleAnnotate: () => void
  annotations: WidgetAnnotation[]
  setNote: (index: number, note: string) => void
  removeAnnotation: (index: number) => void
  sendCorrection: () => Promise<void>
  /** The last failure, shown in the rail. */
  error: string | null
  /** Merge into the host's `useWidgetWire` call — one message listener per frame
   *  stays the contract; this hook never listens to raw `message` itself. */
  wire: Pick<WidgetWireHandlers, 'onEditValues' | 'onEditReady' | 'onAnnotation'>
}

export function useArtifactIteration(
  frameRef: { current: HTMLIFrameElement | null },
  { source, target }: { source: string; target: IterationTarget },
): ArtifactIteration {
  const block = useMemo(() => parseEditModeBlock(source), [source])
  const params = block?.params ?? []
  const paramsKey = params.map((p) => `${p.key}=${p.value}`).join('|')

  const [values, setValues] = useState<Record<string, string>>({})
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [annotating, setAnnotating] = useState(false)
  const [annotations, setAnnotations] = useState<WidgetAnnotation[]>([])

  // Re-seed the RAIL when the source's declared values change (a save landed, or a
  // new artifact version was selected) — the rail follows the document, not vice versa.
  useEffect(() => {
    const seed: Record<string, string> = {}
    for (const p of params) seed[p.key] = p.value
    setValues(seed)
    setDirty(false)
  // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the declared values
  }, [paramsKey])

  const post = useCallback((message: Record<string, unknown>) => {
    frameRef.current?.contentWindow?.postMessage(message, '*')
  }, [frameRef])

  // ── batched live apply ────────────────────────────────────────────────────
  const pending = useRef(new Map<string, string>())
  const frame = useRef<number | null>(null)
  const flush = useCallback(() => {
    frame.current = null
    if (!pending.current.size) return
    const edits = [...pending.current].map(([key, value]) => ({ key, value }))
    pending.current.clear()
    post({ type: EDIT_MODE_SET_KEYS, edits })
  }, [post])

  const setValue = useCallback((key: string, value: string) => {
    // Sanitize at the boundary: this string is about to become a CSS declaration
    // inside the frame AND, on save, a value in the artifact's own source.
    const clean = sanitizeCssValue(value)
    if (!clean) return
    setValues((v) => ({ ...v, [key]: clean }))
    setDirty(true)
    pending.current.set(key, clean)
    if (frame.current === null) frame.current = requestAnimationFrame(flush)
  }, [flush])

  useEffect(() => () => { if (frame.current !== null) cancelAnimationFrame(frame.current) }, [])

  // ── seeding the frame ─────────────────────────────────────────────────────
  // The EDITMODE block is the DECLARATION of each tunable's value, and the renderer
  // owns applying it. Without this, an author would have to write every value twice
  // (once in the block, once in a `:root` rule) — and a SAVED tweak would not survive
  // a reload, because saving rewrites the block and not the stylesheet.
  //
  // It runs when the CHILD says it is ready, not when this hook mounts: the document
  // loads from a blob asynchronously, so anything posted earlier goes to an
  // about:blank window and is lost. Still zero network — one postMessage.
  const declared = useRef<EditModeParam[]>(params)
  declared.current = params
  const onEditReady = useCallback(() => {
    const edits = declared.current.map((p) => ({ key: p.key, value: p.value }))
    if (edits.length) post({ type: EDIT_MODE_SET_KEYS, edits })
  }, [post])

  // ── read-back (the Save half) ─────────────────────────────────────────────
  const waiter = useRef<((values: Record<string, string>) => void) | null>(null)
  const onEditValues = useCallback((live: Record<string, string>) => {
    const resolve = waiter.current
    waiter.current = null
    resolve?.(live)
  }, [])

  const readLiveValues = useCallback((keys: string[]) => {
    return new Promise<Record<string, string>>((resolve, reject) => {
      waiter.current = resolve
      post({ type: EDIT_MODE_READ_KEYS, keys })
      window.setTimeout(() => {
        if (waiter.current !== resolve) return
        waiter.current = null
        reject(new Error('the preview did not report its live values'))
      }, READ_BACK_TIMEOUT_MS)
    })
  }, [post])

  const savable = !!target.persistVersion && params.length > 0
  const save = useCallback(async () => {
    if (!target.persistVersion || saving || !params.length) return
    setSaving(true)
    setError(null)
    try {
      flush() // any drag from this frame must be in the document before we read it
      const live = await readLiveValues(params.map((p) => p.key))
      // Only keys the artifact actually declared: a child that answers with extras
      // must not be able to add properties to the artifact's own source.
      const declared: Record<string, string> = {}
      for (const p of params) {
        const v = live[p.key]
        if (typeof v === 'string' && v.trim()) declared[p.key] = v.trim()
      }
      await target.persistVersion(rewriteEditModeBlock(source, declared))
      setDirty(false)
    } catch (e) {
      setError(`Couldn't save the tweaks: ${String((e as Error)?.message || e)}`)
    } finally {
      setSaving(false)
    }
  }, [target, saving, params, flush, readLiveValues, source])

  // ── annotate ──────────────────────────────────────────────────────────────
  const toggleAnnotate = useCallback(() => {
    setAnnotating((on) => {
      const next = !on
      post({ type: EDIT_MODE_ANNOTATE, on: next })
      return next
    })
  }, [post])

  const onAnnotation = useCallback((annotation: WidgetAnnotation) => {
    setAnnotations((list) => (list.length >= MAX_ANNOTATIONS ? list : [...list, annotation]))
  }, [])

  const setNote = useCallback((index: number, note: string) => {
    setAnnotations((list) => list.map((a, i) => (i === index ? { ...a, note } : a)))
  }, [])

  const removeAnnotation = useCallback((index: number) => {
    setAnnotations((list) => list.filter((_, i) => i !== index))
  }, [])

  const sendCorrection = useCallback(async () => {
    if (!annotations.length) return
    setError(null)
    const directive = composeCorrectionDirective(annotations)
    try {
      if (target.correction) {
        await target.correction(directive)
      } else {
        // Default: the widget bridge. A mounted chat host answers in place;
        // otherwise the shell's launcher opens a chat — the ONE routing path.
        const slug = target.slug
        publishWidgetAction(
          finishActionText(directive, slug ? { saved: true, slug } : undefined),
          slug ? { slug } : {},
        )
      }
      setAnnotations([])
      if (annotating) toggleAnnotate()
    } catch (e) {
      setError(`Couldn't send the correction: ${String((e as Error)?.message || e)}`)
    }
  }, [annotations, target, annotating, toggleAnnotate])

  return {
    params,
    droppedParams: block?.dropped ?? 0,
    values,
    setValue,
    dirty,
    save,
    saving,
    savable,
    annotating,
    toggleAnnotate,
    annotations,
    setNote,
    removeAnnotation,
    sendCorrection,
    error,
    wire: { onEditValues, onEditReady, onAnnotation },
  }
}
