import { useCallback, useEffect, useState } from 'react'
import { useVisiblePoll } from '../../../../lib/useVisiblePoll'
import { useGitStatus } from '../../../files/filesData'

/** A code session's workspace git state, kept live: polled while the worker runs (it edits files
 *  and the supervisor commits each stage), read again after an editor save, and on `refresh`. A
 *  project without a git workspace (its files are the engine's dir) reads nothing. */
export function useWorkspaceChanges(ws: string, isProjectDir: boolean, running: boolean) {
  const [nonce, setNonce] = useState(0)
  const gitWs = isProjectDir ? null : (ws || null)
  const status = useGitStatus(gitWs, nonce)
  const refresh = useCallback(() => { if (gitWs) setNonce((n) => n + 1) }, [gitWs])
  useVisiblePoll(refresh, running && gitWs ? 8000 : null)
  useEffect(() => {
    window.addEventListener('ne:code-file-saved', refresh)
    return () => window.removeEventListener('ne:code-file-saved', refresh)
  }, [refresh])
  return { ...status, gitWs, nonce, refresh }
}
