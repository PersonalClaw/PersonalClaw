import type { WorkflowRunChat } from '../../lib/api'

/** The mark a chat's own run carries on your Runs list (`chat_runs.whose`): whose it is, and what
 *  that means for who reads it and how long it is kept. `null` for a run of yours.
 *
 *  Every clause is a backend fact: only that chat's agent and you read such a run
 *  (`workflows/chat_runs.py`); a Temporary chat's runs are deleted when its session ends, and an
 *  Incognito chat's when you delete the chat (`workflows/private_runs.py`). */
export function chatRunMark(chat: WorkflowRunChat | null | undefined): { label: string; hint: string } | null {
  if (!chat) return null
  const what = chat.batch ? 'batch' : 'run'
  const only = `Only that chat's agent and you see this ${what}`
  if (chat.mode === 'temporary') {
    return { label: `Temporary chat's ${what}`, hint: `${only}. It is deleted when the chat ends.` }
  }
  if (chat.mode === 'incognito') {
    return { label: `Incognito chat's ${what}`, hint: `${only}. It is deleted when you delete the chat.` }
  }
  if (chat.mode) {
    return { label: `A chat's ${what}`, hint: `${only}: that chat's memory setting could not be read.` }
  }
  return { label: "A chat's batch", hint: `${only}: its tasks are that chat's subagents.` }
}
