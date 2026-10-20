import { useEffect, useRef } from 'react'
import type { FsEntry } from '../../../lib/api'
import { FileTree } from '../../files/browse/FileTree'
import { useDirCache } from '../../files/filesData'

// ── The chat's working folder ──────────────────────────────────────────────────────────────────
//
// The files of the folder the chat works in, beside its conversation (⌘⇧E, `useChatPanes`' `?tree`).
// The Files page's tree and its listing, so it shows only what that page may already show, and a
// folder that page may not browse is refused here with the same reason. It only reads: a click on
// a file opens it in the chat's file peek, and no row renames, deletes or takes an upload. It lists
// again when the pane opens and when a turn ends, since the agent may have written there.

const NO_GIT = {}
const NO_ARTIFACTS = new Set<string>()

export function WorkingFolderTree({ dir, openFile, onOpenFile, streaming }: {
  /** The chat's working directory; empty when it has none. */
  dir: string
  /** The file the peek shows, which the tree marks and opens its folders to. */
  openFile: string | null
  onOpenFile: (path: string) => void
  /** Whether a turn is running: when one ends, the folder is listed again. */
  streaming: boolean
}) {
  const dirs = useDirCache()
  const { invalidateSubtree } = dirs
  // A listing the cache painted is the one from before: drop it, and the tree asks for the folder as
  // it is now while it keeps showing the old one. With nothing cached the tree's own first read is
  // the fresh one, and dropping the slot it is about to fill would only discard that read.
  useEffect(() => { if (dir && dirs.cache[dir]) invalidateSubtree(dir) }, [dir, invalidateSubtree]) // eslint-disable-line react-hooks/exhaustive-deps
  const wasStreaming = useRef(streaming)
  useEffect(() => {
    if (wasStreaming.current && !streaming && dir) invalidateSubtree(dir)
    wasStreaming.current = streaming
  }, [streaming, dir, invalidateSubtree])

  if (!dir) {
    return (
      <div className="flex flex-col gap-xs px-m py-l">
        <p data-type="body-s" className="text-on-surface">This chat has no working folder.</p>
        <p data-type="caption" className="text-on-surface-low">Set one with Working directory, in the chat's header.</p>
      </div>
    )
  }
  return (
    <div className="flex flex-col">
      <p data-type="caption" className="truncate px-m pb-xs pt-s text-on-surface-low" title={dir}>{dir}</p>
      <FileTree dirs={dirs} rootPath={dir} activePath={openFile} gitStatuses={NO_GIT} artifactPaths={NO_ARTIFACTS}
        onOpenFile={(entry: FsEntry) => onOpenFile(entry.path)} emptyLabel="The working folder is empty." />
    </div>
  )
}
