// The canonical GitHub blob root for repo docs, matching pyproject.toml's
// [project.urls] Source. Reader-facing doc deep links (the benchmark methodology,
// the ACP parity statement) resolve against it, so the URL lives in ONE place —
// an org rename or a move off the `main` default branch is a single edit here,
// not a hunt across every panel that links a doc.
export const REPO_DOC_BLOB_ROOT = 'https://github.com/PersonalClaw/PersonalClaw/blob/main/'

/** Absolute blob URL for a repo-relative doc path, e.g.
 *  `repoDocUrl('docs/agents/acp-parity.md')`. */
export function repoDocUrl(path: string): string {
  return `${REPO_DOC_BLOB_ROOT}${path}`
}

/** The releases page: where a new version of an install the gateway does not update in place comes
 *  from (the desktop app's dmg, AppImage and deb), as `POST /api/update`'s `instructions` answer says. */
export const RELEASES_PAGE_URL = 'https://github.com/PersonalClaw/PersonalClaw/releases'
