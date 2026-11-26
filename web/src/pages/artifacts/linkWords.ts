import type { Artifact, LinkMissed } from '../../lib/api'

// ── What the viewer says when an artifact's link missed its file ────────────────────────────────
//
// A file-backed artifact follows a file: it shows the file's text, and saving it writes the file.
// When a read could not follow the file (`source_unfollowed`), the viewer shows the artifact's last
// saved copy and says so, naming the file and why. When a save could not write it
// (`source_unwritten`), the save is kept as the artifact's own copy, and the notice says the file
// did not change.

const WHY: Record<LinkMissed, string> = {
  outside: 'it is outside the folders an artifact can follow',
  missing: 'there is no file there',
  unreadable: 'it could not be read',
  unwritable: 'it could not be written',
}

/** The bar over an artifact that no longer follows its file. A file moved into the workspace, at
 *  the same place in it, links it again when Files saves it as an artifact (the gateway's
 *  `find_link_for`), and so does a file put back where it was. */
export function notFollowedLine(path: string, why: LinkMissed): string {
  const again = why === 'outside' || why === 'missing'
    ? ' To link it again, save the file from your workspace as an artifact in Files.'
    : ''
  return `This no longer follows ${path}: ${WHY[why]}. It shows its last saved copy, and saving changes only that copy.${again}`
}

/** The notice after a save that kept its text here but did not write the file. */
export function notWrittenLine(path: string, why: LinkMissed): string {
  return `Saved here, but ${path} did not change: ${WHY[why]}.`
}

/** Says *answer*'s missed write through *say*, and hands *answer* on. */
export function sayIfNotWritten<T>(answer: T, say: (line: string) => void): T {
  const a = answer as Partial<Artifact> | null | undefined
  if (a?.source_unwritten && a.source_path) say(notWrittenLine(a.source_path, a.source_unwritten))
  return answer
}
