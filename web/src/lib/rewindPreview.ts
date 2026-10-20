import type { RewindFileWire, RewindPreviewWire } from './api'

/** Why a file the agent touched was never backed up, for each `skipped` reason the checkpoint
 *  store records (`turn_checkpoints.capture_pre_edit`, `back_up_named_files`). */
const NEVER_BACKED_UP: Record<string, string> = {
  secret: 'a credential file, which is never copied',
  too_large: 'larger than the biggest file a backup keeps',
  over_cap: 'it did not fit in the backups kept for this chat',
  outside: 'outside this chat’s folders, where a rewind does not write',
  directory: 'a folder, not a file',
  unreadable: 'it could not be read when it was to be backed up',
  'blob missing': 'its backup is gone',
  'blob unreadable': 'its backup could not be read',
}

/** What `/rewind-to-turn N`'s preview says about one file: what the rewind does to it, and for one
 *  it cannot put back, why. A file that changed with no backup at all (a shell command's change,
 *  an agent CLI's edit made without asking) says what happened to it and that it stays so; one
 *  whose backup came after such a change says the rewind goes back only as far as that backup. */
export function rewindFileLine(f: RewindFileWire, turn: number): string {
  const p = `\`${f.path}\``
  if (f.action === 'not_captured') {
    if (f.reason === 'changed') return `- ${p} — changed after turn ${turn} with no backup; it stays as it is now`
    if (f.reason === 'created') return `- ${p} — created after turn ${turn} with no backup; it is not removed`
    if (f.reason === 'deleted') return `- ${p} — deleted after turn ${turn} with no backup; it is not brought back`
    return `- ${p} — never backed up (${NEVER_BACKED_UP[f.reason] ?? f.reason}); it will not be restored`
  }
  const partly = f.reason === 'changed_before_backup'
  if (f.action === 'delete') {
    return partly
      ? `- ${p} — would be DELETED, as it was before the agent wrote it in turn ${f.turn}; the file there at turn ${turn} was removed with no backup and is not brought back`
      : `- ${p} — would be DELETED (it did not exist at turn ${turn})`
  }
  if (f.action === 'unchanged') return `- ${p} — already matches turn ${turn}; no change`
  return partly
    ? `- ${p} — restore ${f.current_size} → ${f.restored_size} bytes, back to how it was before the agent changed it in turn ${f.turn}; what changed it before that had no backup, and stays`
    : `- ${p} — restore ${f.current_size} → ${f.restored_size} bytes`
}

/** The preview as the chat shows it: the warnings, then a line per file (`rewindFileLine`), then how
 *  to apply it. Blocks are apart, as Markdown needs them: a sentence right under a list reads as part
 *  of its last item, and warnings on adjacent lines would read as one. */
export function rewindPreviewText(p: RewindPreviewWire, turn: number): string {
  const files = p.files || []
  return [
    `**Rewind to turn ${turn} — preview.** Nothing has been written yet.`,
    ...(p.warnings || []).map((w) => `> ${w}`),
    files.length ? files.map((f) => rewindFileLine(f, turn)).join('\n') : '_No recorded file changes after that turn._',
    `Run \`/rewind-to-turn ${turn} --confirm\` to apply. This restores files only — the conversation stays as the record of what happened.`,
  ].join('\n\n')
}
