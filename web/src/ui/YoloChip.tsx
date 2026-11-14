import { useCallback, useEffect, useState } from 'react'
import { ShieldAlert } from 'lucide-react'
import { api } from '../lib/api'
import { useChatSocket } from '../lib/useChatSocket'
import { refreshKinds, type WsMessage } from '../lib/wsFrames'
import { onYoloChanged } from '../lib/yoloSignal'
import { Popover } from './Popover'
import { TextLink } from './TextLink'

/** What YOLO means, said wherever it is on. */
export const YOLO_SENTENCE = 'Your chats run tool calls without asking.'

/** The chip's name at every width: the visible words first, then what they mean. */
export const YOLO_NAME = 'YOLO active: your chats run tool calls without asking'

/** The YOLO mark: while YOLO is on, the one fact about the agent's permissions you must see wherever
 *  it can act, so it sits in the shell's right corner beside what waits on you, on every page and
 *  in focus mode. Nothing shows while it is off.
 *
 *  From 768 px it reads "YOLO active" in warn; in the corner's fold (*folded*) it is a glyph named
 *  for the whole sentence. Either way it is a disclosure: a press opens the sentence and the way to
 *  change it, since a tooltip never opens on touch. It reads `GET /api/status` on mount, again when
 *  the gateway rings `refresh` kind `yolo`, when YOLO is switched here (`setAgentYolo`), and when a
 *  timed YOLO's time is up. */
export function YoloChip({ folded = false, className }: { folded?: boolean; className?: string }) {
  const [on, setOn] = useState(false)
  const [expiresIn, setExpiresIn] = useState<number | null>(null)

  const load = useCallback(() => {
    api.status().then((s) => {
      setOn(Boolean(s?.yolo))
      setExpiresIn(s?.yolo && typeof s.yolo_expires_in === 'number' && s.yolo_expires_in > 0 ? s.yolo_expires_in : null)
    }).catch(() => {})
  }, [])
  useEffect(load, [load])
  useEffect(() => onYoloChanged(load), [load])
  useChatSocket((m: WsMessage) => { if (refreshKinds(m).includes('yolo')) load() }, load)
  // A timed YOLO ends on its own: read again once its time is up (and a second after, so the
  // gateway has turned it off by then).
  useEffect(() => {
    if (expiresIn === null) return
    const timer = window.setTimeout(load, (expiresIn + 1) * 1000)
    return () => window.clearTimeout(timer)
  }, [expiresIn, load])

  if (!on) return null
  const tint = { background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)', color: 'var(--color-warn)' }
  return (
    <Popover align="right" width={280} placement="bottom" portal
      trigger={(open, toggle) => (
        <button type="button" onClick={toggle} aria-expanded={open} aria-label={YOLO_NAME} data-yolo-chip=""
          data-type="caption"
          className={`flex min-h-6 items-center justify-center gap-1.5 rounded-pill transition-colors hover:brightness-110 ${folded ? 'px-1.5' : 'px-2.5 py-1'}${className ? ` ${className}` : ''}`}
          style={tint}>
          <ShieldAlert size={13} className="shrink-0" aria-hidden="true" />
          {!folded && <span>YOLO active</span>}
        </button>
      )}>
      {() => (
        <div className="flex flex-col gap-s p-xs">
          <p data-type="body-s" className="text-on-surface">{YOLO_SENTENCE}</p>
          <TextLink href="#/settings/agent" ink="emphasis" size="sm">Change it in Agent defaults</TextLink>
        </div>
      )}
    </Popover>
  )
}
