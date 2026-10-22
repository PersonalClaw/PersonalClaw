import { useCallback } from 'react'
import { api } from '../../../../lib/api'
import { promptForm } from '../../../../ui/dialog'
import { type CodeToastState } from '../../CodeToast'

// ── Committing a code session's changes ───────────────────────────────────────────────────────
//
// Its owner commits what the session's workspace holds and has not committed, with a message, from
// the cockpit's header (`POST /api/loops/{id}/commit`). The dialog says what it commits and that it
// pushes nothing. A commit is a write inside the workspace, so the dialog's Commit is the one press
// (#3822's second confirmation is for a delete, an overwrite outside the workspace, a send to other
// people and spending). The outcome is said in the cockpit's toast, and the git state is read again.

/** Said on the control and in its dialog: the gateway's git runs no program the repository names,
 *  a hook included (an agent can write `.git/hooks`), so the repository's hooks run only from a
 *  terminal. */
export const COMMIT_HOOKS_NOTE = "Commits here don't run the repository's git hooks; commit from your terminal to run them."

/** The longest message the commit takes, as the route caps it. */
export const COMMIT_MESSAGE_CAP = 5000

export function useCommit({ id, ws, setToast }: { id: string; ws: string; setToast: (t: CodeToastState | null) => void }) {
  return useCallback(async () => {
    const got = await promptForm({
      title: 'Commit the changes',
      body: `Every uncommitted change in ${ws}, new files included, is committed as git is set up to commit there. Nothing is pushed. ${COMMIT_HOOKS_NOTE}`,
      confirmLabel: 'Commit',
      fields: [{
        name: 'message', label: 'Message', type: 'textarea', required: true,
        validate: (v) => (v.trim().length > COMMIT_MESSAGE_CAP ? `A commit message is at most ${COMMIT_MESSAGE_CAP} characters` : null),
      }],
    })
    const message = got?.message?.trim()
    if (!message) return
    try {
      const r = await api.loopCommit(id, message)
      setToast({ kind: 'ok', text: `Committed ${r.sha.slice(0, 7)}` })
      window.dispatchEvent(new CustomEvent('ne:code-committed'))
    } catch (e) {
      setToast({ kind: 'error', text: `Couldn't commit: ${(e as Error)?.message || 'unknown error'}` })
    }
  }, [id, ws, setToast])
}
