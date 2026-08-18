import { useCallback, useEffect, useRef, useState } from 'react'
import { FileWarning, MessagesSquare, RotateCcw, X } from 'lucide-react'
import { fvs } from '../../design/fontWeight'
import { api } from '../../lib/api'
import { ChatEmbed } from '../../app/appSdk'
import { IconButton } from '../../ui/IconButton'
import { QuietButton } from '../../ui/QuietButton'
import { Loading } from '../../ui/ListScaffold'

/** The sentinel the host writes to `?iterate` to mean "open a panel, it still needs
 *  a session". The panel replaces it with the real session key, which makes the
 *  open thread deep-linkable — the same rule the rest of this surface follows
 *  (selection state IS the URL). */
export const ITERATE_PENDING = 'new'

/** AE-10 — the chat half of the split-view iterate panel.
 *
 *  Staging is INVESTIGATE-ANYWHERE's primitive, not a second mechanism:
 *  `POST /api/investigate {kind:'artifact'}` composes the envelope server-side
 *  (AE-7's resolver — fenced current content, `agent` task mode so the agent may
 *  call `artifact_update` on this one slug, and an opening prompt that names the
 *  slug) and hands back a staged session. This renders that session with
 *  `ChatEmbed`, so the panel is the host's own chat surface in an iframe rather
 *  than a third chat implementation.
 *
 *  The embed is a separate document with no bridge back to the page, so the
 *  detail view learns about new versions from the socket instead — see
 *  `artifactUpdateSignal`. */
export function ArtifactIteratePanel({ slug, name, session, onSession, onClose }: {
  slug: string
  /** The artifact's display name — the panel's accessible name, so two open
   *  panels (or a panel beside the chat page) never announce identically. */
  name: string
  /** `ITERATE_PENDING` to stage a new session, else a session key to resume. */
  session: string
  onSession: (key: string) => void
  onClose: () => void
}) {
  const [staged, setStaged] = useState<{ key: string; prompt: string } | null>(
    session && session !== ITERATE_PENDING ? { key: session, prompt: '' } : null,
  )
  const [error, setError] = useState('')
  // Exactly one investigate POST per open. Reporting the key back up rewrites
  // `?iterate`, which re-renders us — without this latch that would stage a
  // second session on every open.
  const requested = useRef(false)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])

  const start = useCallback(async () => {
    requested.current = true
    setError('')
    try {
      const res = await api.investigate({ kind: 'artifact', id: slug, back_link: `#/artifacts/${slug}` })
      if (!alive.current) return
      // The opening prompt PRE-FILLS the composer rather than sending itself: the
      // user always fires the first turn, exactly as the full-page investigate
      // path does.
      setStaged({ key: res.session_key, prompt: res.context.opening_prompt || '' })
      onSession(res.session_key)
    } catch (e) {
      if (alive.current) setError(String((e as Error)?.message || e))
    }
    // onSession is the host's URL writer; it changes identity on every render and
    // must not restart staging.
  }, [slug])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (!staged && !requested.current) start() }, [staged, start])

  return (
    // `flex-1` is load-bearing, not decoration: without it the aside's height is its
    // CONTENT height, the embed's `height: 100%` resolves against nothing, and an
    // iframe with no resolved height falls back to its 150px intrinsic default. The
    // panel then renders as a 150px letterbox with a chat in it — measured, not
    // guessed (the render tests cannot see this; a browser drive can).
    <aside aria-label={`Iterate with agent: ${name}`}
      className="flex min-h-0 min-w-0 flex-1 flex-col border-outline/40 border-t lg:border-t-0 lg:border-l">
      <div className="flex shrink-0 items-center gap-2 border-b border-outline/40 px-m py-2">
        <MessagesSquare size={14} className="shrink-0 text-primary" />
        <span className="truncate text-on-surface text-[0.8125rem]" style={fvs(500)}>Iterate with agent</span>
        <div className="ml-auto shrink-0">
          <IconButton icon={X} label="Close the iterate panel" title="Close" size={24} iconSize={13} onClick={onClose} />
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col">
        {/* A failed stage is its OWN state: without it the panel would sit on the
            loading line forever and read as a chat that never arrived. */}
        {error ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-m text-center">
            <FileWarning size={22} className="opacity-40 text-on-surface-low" />
            <p className="text-on-surface text-[0.8125rem]">Couldn't open an iteration session.</p>
            <p className="text-on-surface-low text-[0.75rem]">{error}</p>
            <QuietButton onClick={() => start()} title="Retry opening the iteration session">
              <RotateCcw size={13} /> Try again
            </QuietButton>
          </div>
        ) : staged ? (
          <ChatEmbed session={staged.key} prompt={staged.prompt || undefined} className="min-h-0 flex-1" />
        ) : (
          <div className="flex flex-1 items-center justify-center"><Loading what="the iteration session" /></div>
        )}
      </div>
    </aside>
  )
}
