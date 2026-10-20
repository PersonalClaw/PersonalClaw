import { useCallback, useEffect, useState } from 'react'
import { reportingWrite } from '../app/reportingWrite'
import { api, ApiError, type ModelWait } from './api'
import { refreshKinds, useChatSocket, type WsMessage } from './useChatSocket'

/** A wait as a page shows it: `movesOnAt` is when it moves on to its next model, in this tab's
 *  clock (`left_secs` counted from when the list was read), `null` when it waits as long as it
 *  takes. */
export interface ShownWait extends ModelWait {
  movesOnAt: number | null
}

/** The requests you are waiting for while a local model is busy, kept current.
 *
 *  Read once, then again on every `refresh` frame naming `model_waits` (the gateway sends one
 *  whenever a wait starts or ends) and after a reconnect, since a hint sent while the socket was
 *  down is lost. `moveOn` stops one wait now so its next model answers. */
export function useModelWaits(): { waits: ShownWait[]; moveOn: (id: string) => Promise<void> } {
  const [waits, setWaits] = useState<ShownWait[]>([])
  const load = useCallback(() => {
    api.modelWaits()
      .then((r) => {
        const now = Date.now()
        setWaits((r.waits ?? []).map((w) => ({
          ...w,
          movesOnAt: w.left_secs === null ? null : now + w.left_secs * 1000,
        })))
      })
      .catch(() => {})
  }, [])
  useEffect(load, [load])
  useChatSocket((m: WsMessage) => { if (refreshKinds(m).includes('model_waits')) load() }, load)
  // A wait that ended before the click answers 404, which is no failure: the re-read shows what
  // is true now. Any other refusal is said.
  const moveOn = useCallback(async (id: string) => {
    await reportingWrite('ask the next model now', async () => {
      try {
        await api.moveOnModelWait(id)
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404)) throw e
      }
    })
    load()
  }, [load])
  return { waits, moveOn }
}
