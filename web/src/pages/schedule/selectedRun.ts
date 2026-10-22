import { createContext, useContext, useState } from 'react'

/** The run an automation's history has open, held by the page that lists the automation, so the
 *  run is in its address (`?run=<run id>` on Automations, #3816): a reload, Back and Forward and a
 *  copied link open the same run. */
export interface RunSelection {
  readonly run: string | null
  readonly setRun: (runId: string | null) => void
}

export const SelectedRun = createContext<RunSelection | null>(null)

/** The open run and its setter: the page's, where a page holds it, else this history's own. */
export function useSelectedRun(): [string | null, (runId: string | null) => void] {
  const held = useContext(SelectedRun)
  const [own, setOwn] = useState<string | null>(null)
  return held ? [held.run, held.setRun] : [own, setOwn]
}
