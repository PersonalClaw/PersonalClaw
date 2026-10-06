/** The chat header's Working directory prompt, and what the composer says once it is set.
 *
 *  The prompt opens on the folder the chat works in, so Set with nothing changed is no change, and
 *  an empty path is a clear: the chat goes back to the workspace a new chat starts in. A chat's
 *  runtime works in its folder from the turn after the change; a turn running when it lands ends
 *  there, and the chat says what happens to her message (`running_turn.rebind` in the gateway). */
import type { PromptOptions } from '../../ui/dialog'

/** The gateway's answer to setting the folder (`POST /api/chat/sessions/{session}/workspace-dir`). */
export interface WorkingDirectoryAnswer {
  workspace_dir?: string
  /** A turn was running when the change landed: it ends, and the change is made when it has. */
  moved?: boolean
}

export function workingDirectoryPrompt(current: string): PromptOptions {
  return {
    title: 'Working directory',
    label: 'Absolute path for the agent’s working directory (leave it empty to work in the workspace)',
    placeholder: '/Users/you/project',
    initial: current,
    confirmLabel: 'Set',
    required: false,
  }
}

/** What the composer says: where the chat works now, and, when a turn was running, that it ends
 *  and does nothing more in the folder the chat left. */
export function workingDirectoryToast(answer: WorkingDirectoryAnswer, cleared: boolean): string {
  const folder = answer.workspace_dir ?? ''
  const where = cleared
    ? `Working directory cleared: this chat works in the workspace${folder ? ` (${folder})` : ''}.`
    : `Working directory set to ${folder}.`
  return answer.moved
    ? `${where} The turn that was running ends now; nothing more of it runs in the old folder.`
    : where
}
