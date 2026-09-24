/** Imperative, styled dialogs — the app-wide replacement for the browser's
 *  native window.confirm / window.prompt / window.alert.
 *
 *  Usage (callable from anywhere — event handlers, catch blocks, plain modules):
 *
 *      import { confirm, confirmDelete, promptInput, alertDialog } from '../../ui/dialog'
 *
 *      if (!(await confirm({ title: 'Apply update?', body: '…' }))) return
 *      if (!(await confirmDelete('schedule', job.name))) return
 *      const name = await promptInput({ title: 'New file', label: 'Name' })
 *      await alertDialog({ title: 'Could not save', body: err.message, tone: 'danger' })
 *
 *  A single <DialogHost> (mounted in the app shell) renders them. */
export {
  confirm,
  promptInput,
  promptForm,
  alertDialog,
  openDialog,
  type ConfirmOptions,
  type PromptOptions,
  type FormOptions,
  type AlertOptions,
  type DialogField,
} from './dialogStore'

import { confirm } from './dialogStore'

/** Convenience for the dominant pattern — a destructive delete confirmation.
 *  `entity` is the noun ("schedule", "agent"); `name` is the specific item.
 *  Renders a danger-tinted dialog and resolves true when the user confirms. */
export function confirmDelete(entity: string, name?: string, opts?: { body?: React.ReactNode; confirmLabel?: string }): Promise<boolean> {
  const label = name ? `${entity} "${name}"` : `this ${entity}`
  return confirm({
    title: `Delete ${label}?`,
    body: opts?.body ?? 'This cannot be undone.',
    danger: true,
    confirmLabel: opts?.confirmLabel ?? 'Delete',
  })
}

/** The same bar as `confirmDelete`, for a destructive action whose verb is NOT "delete".
 *
 *  🔑 DELETE WAS NEVER THE ONLY DESTRUCTIVE VERB, and `confirmDelete` hardcodes its title and
 *  button, so the actions that destroy state under another name had no shared way to ask. Both
 *  first callers were shipping ungated *beside* a gated sibling: tag **merge** deletes the source
 *  tag from the taxonomy (strictly more than `delete_tag`, which does confirm, four lines away),
 *  and repeatable-list **reset** is the only path that empties `execution_notes`.
 *
 *  🪤 `body` is required, not optional. `confirmDelete` can default to "This cannot be undone."
 *  because its title already names the object and the verb is understood; a reset or a merge does
 *  not read as destructive from its verb at all, so the prompt has to state the blast radius or it
 *  is a speed bump rather than a decision. Pass the measured counts, not an adjective. */
export function confirmDestructive(
  title: string,
  body: React.ReactNode,
  opts?: { confirmLabel?: string },
): Promise<boolean> {
  return confirm({ title, body, danger: true, confirmLabel: opts?.confirmLabel ?? 'Continue' })
}
