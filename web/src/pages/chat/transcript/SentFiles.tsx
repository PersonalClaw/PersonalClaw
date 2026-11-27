import { Download, FileText, FolderOpen } from 'lucide-react'
import { fvs } from '../../../design/fontWeight'
import { TextLink } from '../../../ui/TextLink'
import { SignedTextLink } from '../../../ui/SignedMedia'
import { api } from '../../../lib/api'
import { reportActionFailure } from '../../../app/reportingWrite'
import { humanBytes } from '../../../lib/chunkedUpload'
import type { FileSegment } from '../chatTypes'

/** What a sent file's card says under its name: what the agent said it is, and its size. */
function captionOf(file: FileSegment): string {
  const size = typeof file.size === 'number' && file.size > 0 ? humanBytes(file.size) : ''
  return [file.description, size].filter(Boolean).join(' · ') || 'File'
}

/** The files the agent sent in a turn (`notify_attachment`), each a card under its answer: its
 *  name, what the agent said it is and its size, opened with the app that opens its kind on the
 *  gateway's computer, or downloaded from the outbox. Never folded away with the steps: it is what
 *  the turn sent, not how. */
export function SentFiles({ files }: { files: FileSegment[] }) {
  if (!files.length) return null
  return (
    <ul aria-label="Files sent in this turn" className="mt-s flex flex-col gap-xs">
      {files.map((f, i) => (
        <li key={`${f.ts || i}-${f.filename}`} className="flex min-w-0 items-center gap-s rounded-xl border border-outline-variant px-m py-s">
          <FileText size={15} aria-hidden className="shrink-0 text-on-surface-low" />
          <span className="min-w-0 flex-1">
            <span data-type="label-s" className="block truncate text-on-surface" style={fvs(500)}>{f.filename}</span>
            <span data-type="caption" className="block truncate text-on-surface-low">{captionOf(f)}</span>
          </span>
          {f.path && (
            <TextLink onClick={() => { void api.revealPath(f.path as string, 'open').catch(reportActionFailure(`open ${f.filename}`)) }} size="xs" icon={FolderOpen}
              iconSize={12} className="shrink-0" aria-label={`Open ${f.filename}`}>
              Open
            </TextLink>
          )}
          <SignedTextLink href={`/api/outbox/${encodeURIComponent(f.filename)}`} download={f.filename} size="xs" icon={Download}
            iconSize={12} className="shrink-0" aria-label={`Download ${f.filename}`}>
            Download
          </SignedTextLink>
        </li>
      ))}
    </ul>
  )
}
