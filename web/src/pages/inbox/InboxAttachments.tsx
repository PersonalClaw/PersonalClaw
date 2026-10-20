import { Download, Paperclip } from 'lucide-react'
import { fvs } from '../../design/fontWeight'
import { api, type InboxItem } from '../../lib/api'
import { humanBytes } from '../../lib/chunkedUpload'
import { downloadFrom } from '../../lib/download'
import { Button } from '../../ui/Button'
import { InboxSection as Section } from './InboxSection'

/** The files a message came with: each one's name, type and size, and Download for each one that
 *  was kept. One that was not kept (too large, past the most one message keeps) says why instead.
 *
 *  A file downloads and is never opened in the page: the gateway serves it as a file to save
 *  whatever type the message said it was, because the sender chose that type. */
export function InboxAttachments({ item }: { item: InboxItem }) {
  const files = item.attachments ?? []
  if (files.length === 0) return null
  return (
    <Section label={`Attachments · ${files.length}`}>
      <ul className="flex flex-col gap-xs" aria-label="Attachments">
        {files.map((file) => (
          <li key={file.id} className="flex items-center gap-s rounded-md bg-surface-container/60 px-m py-xs">
            <Paperclip size={14} aria-hidden className="shrink-0 text-on-surface-low" />
            <div className="min-w-0 flex-1">
              <p data-type="body-s" className="break-all text-on-surface" style={fvs(600)}>{file.name}</p>
              <p data-type="caption" className="text-on-surface-low">
                {file.mimetype || 'type not given'} · {humanBytes(file.size)}
              </p>
              {!file.kept && (
                <p data-type="caption" className="text-on-surface-low">
                  Not kept: {file.not_kept || 'it could not be saved'}
                </p>
              )}
            </div>
            {file.kept && (
              <Button size="sm" variant="ghost" className="shrink-0" ariaLabel={`Download ${file.name}`}
                onClick={() => downloadFrom(api.inboxAttachmentUrl(item.id, file.id))}>
                <Download size={14} /> Download
              </Button>
            )}
          </li>
        ))}
      </ul>
    </Section>
  )
}

/** How many files a row's message came with, on the row itself, so a message with an attachment
 *  reads as one before it is opened. Nothing for a message with none. */
export function AttachmentCount({ item }: { item: InboxItem }) {
  const n = item.attachments?.length ?? 0
  if (n === 0) return null
  return (
    <span data-type="caption" className="shrink-0 inline-flex items-center gap-xs text-on-surface-low">
      <Paperclip size={11} aria-hidden /> {n} attached
    </span>
  )
}
