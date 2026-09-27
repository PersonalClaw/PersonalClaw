import { useEffect, useState } from 'react'
import { ExternalLink, FileText, Image as ImageIcon, Loader2, Paperclip, X } from 'lucide-react'
import { api, type ChatImageInput } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { Modal } from '../../ui/Modal'
import { TextLink } from '../../ui/TextLink'
import { FieldError } from '../../ui/forms'
import { attachedName } from '../files/fileMeta'
import { imagesAsTextNote, UNREAD_NO_IMAGE_MODEL, type ImageDelivery } from './imageAttachments'

export interface AttachmentChipsProps {
  paths: string[]
  /** The attached (uploaded) paths that go down the image path. */
  images: string[]
  session: string
  agent: string
  model: string
  /** The ACP runtime the composer's agent pick runs on, or "" for the native loop. */
  runtime: string
  onRemove: (p: string) => void
  onOpen: (p: string) => void
}

/** The composer's attachment chips. With an image attached, it asks the server how images reach
 *  the model this chat's next turn is served by, and says so when they go as text. */
export function AttachmentChips(props: AttachmentChipsProps) {
  if (!props.images.length) return <MentionChips paths={props.paths} onRemove={props.onRemove} onOpen={props.onOpen} />
  return <ImageAttachmentChips {...props} />
}

function ImageAttachmentChips({ paths, images, session, agent, model, runtime, onRemove, onOpen }: AttachmentChipsProps) {
  const { data, error } = useQuery(
    `chat:image-input:${session}:${runtime || agent}:${model}`,
    () => api.chatImageInput(session, runtime ? { runtime, agent } : { agent, model }),
  )
  const asText = !!data && !data.accepted
  return (
    <>
      <MentionChips paths={paths} images={images} asText={asText ? images : []} onRemove={onRemove} onOpen={onOpen} />
      {data === undefined && error ? (
        // The turn still decides from the server's record; what failed is only the chip's question.
        <FieldError className="-mt-1 mb-2">Couldn't check how images reach this chat's model — {(error as Error)?.message || 'the server did not respond'}</FieldError>
      ) : asText && <ImagesAsTextNote input={data} images={images} />}
    </>
  )
}

/** What a model that takes no images gets instead: the text read from each image, or — when
 *  nothing could read one — only its size and format, and when that is because no image model is
 *  set up, where to set one up. Read from the images' own extraction, which this asking starts (an
 *  image is read only when its text is wanted, `AttachmentExtractor.start`), so
 *  the sentence is about these files, not about what might be installed. */
function ImagesAsTextNote({ input, images }: { input: ChatImageInput; images: string[] }) {
  const { data, error } = useQuery(
    `chat:attachment-extract:${images.join('|')}`,
    () => Promise.all(images.map((p) => api.attachmentExtract(p))),
  )
  if (data === undefined && error) {
    return (
      <FieldError className="-mt-1 mb-2">{input.reason} Couldn't read what it would get from {images.length === 1 ? 'the image' : 'the images'} — {(error as Error)?.message || 'the server did not respond'}</FieldError>
    )
  }
  const got = data && {
    read: data.every((x) => x.read),
    noImageModel: data.every((x) => !x.read && x.unread === UNREAD_NO_IMAGE_MODEL),
  }
  const note = imagesAsTextNote(input, images.length, got)
  if (!note) return null
  return (
    <p role="note" className="-mt-1 mb-2 text-[0.75rem] text-on-surface-var">
      {note}{got?.noImageModel && <> <SetUpAnImageModel /></>}
    </p>
  )
}

/** Where an image model is set up — said wherever no image model is, so the fix is one click. */
function SetUpAnImageModel() {
  return (
    <>Choose one in{' '}
      <TextLink href="#/settings/models" ink="emphasis" className="underline">Settings → Models</TextLink>.
    </>
  )
}

/** Highlighted chips for @-mentioned files, shown ABOVE the composer. Clicking
 *  the chip reveals the FULL path inline (so the user knows exactly which file)
 *  and offers Open (file panel); ✕ removes the attachment. An attached image shows an image
 *  glyph, and "as text" when its text, not the image, will reach the model. */
function MentionChips({ paths, images = [], asText = [], onRemove, onOpen }: { paths: string[]; images?: string[]; asText?: string[]; onRemove: (p: string) => void; onOpen: (p: string) => void }) {
  const [expanded, setExpanded] = useState<string | null>(null)
  if (!paths.length) return null
  return (
    <div className="mb-2 flex flex-wrap gap-2">
      {paths.map((p) => {
        const open = expanded === p
        return (
          <div key={p} className="flex items-center gap-1.5 rounded-lg border border-primary/40 bg-primary/10 px-2.5 py-1.5 text-[0.8125rem]"
            style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>
            {images.includes(p) ? <ImageIcon size={13} className="shrink-0 text-primary" /> : <FileText size={13} className="shrink-0 text-primary" />}
            {/* An accordion, so `aria-expanded` — the chip swaps a basename for the full path AND
                reveals an Open button, both gated on the same flag. */}
            <button type="button" aria-expanded={open} onClick={() => setExpanded(open ? null : p)}
              title={open ? 'Collapse' : 'Show full path'}
              className="min-w-0 text-left font-mono text-on-surface">
              {open ? <span className="break-all">{p}</span> : attachedName(p)}
            </button>
            {asText.includes(p) && <span className="shrink-0 text-[0.75rem] text-on-surface-var">as text</span>}
            {open && (
              <Button variant="ghost-accent" size="xs" title="Open file" onClick={() => onOpen(p)} ariaLabel={`Open ${attachedName(p)}`}
                className="shrink-0 h-6 px-1.5 text-[0.75rem]">Open</Button>
            )}
            <IconButton icon={X} label="Remove file" onClick={() => onRemove(p)} size={20} iconSize={13}
              tone="danger" className="shrink-0" />
          </div>
        )
      })}
    </div>
  )
}

/** Attachment chips shown on a SENT user turn (right-aligned under the bubble),
 *  one per file attached to that turn. Clicking a chip opens a preview modal:
 *  the EXTRACTED content the agent saw (fetched on open) + a button to open the
 *  ORIGINAL file in the file panel. So the user can always see what they attached
 *  and exactly what was fed to the model. */
export function TurnAttachments({ paths, delivery, onOpenFile }: { paths: string[]; delivery?: ImageDelivery; onOpenFile: (p: string) => void }) {
  const [peek, setPeek] = useState<string | null>(null)
  return (
    <div className="mt-1.5 flex flex-wrap justify-end gap-1.5">
      {paths.map((p) => {
        const how = delivery?.byPath[p]
        return (
          <button key={p} type="button" onClick={() => setPeek(p)}
            title={how === 'text' ? `Preview ${attachedName(p)} — sent as text${delivery?.reason ? `: ${delivery.reason}` : ''}` : `Preview ${attachedName(p)}`}
            className="inline-flex items-center gap-1.5 rounded-pill border border-outline-variant/50 bg-surface-container px-2.5 py-1 text-[0.75rem] text-on-surface-var transition-colors hover:bg-surface-high hover:text-on-surface">
            {how ? <ImageIcon size={11} className="shrink-0 text-on-surface-low" /> : <Paperclip size={11} className="shrink-0 text-on-surface-low" />}
            <span className="max-w-[200px] truncate">{attachedName(p)}</span>
            {how === 'text' && <span className="shrink-0 text-on-surface-low">· sent as text</span>}
          </button>
        )
      })}
      {peek && <AttachmentPeekModal path={peek} name={attachedName(peek)} delivery={delivery?.byPath[peek]} reason={delivery?.reason} onOpenFile={onOpenFile} onClose={() => setPeek(null)} />}
    </div>
  )
}

/** Preview an attachment: its extracted text content (what the agent saw) +
 *  open-original. Extraction is fetched on open (it awaits the job already running, or starts it). */
function AttachmentPeekModal({ path, name, delivery, reason, onOpenFile, onClose }: { path: string; name: string; delivery?: 'image' | 'text'; reason?: string; onOpenFile: (p: string) => void; onClose: () => void }) {
  const [text, setText] = useState<string | null>(null)
  const [read, setRead] = useState(true)
  const [unread, setUnread] = useState('')
  const [readErr, setReadErr] = useState<unknown>(null)
  const [loading, setLoading] = useState(delivery !== 'image')
  useEffect(() => {
    // An image the model was SHOWN has no "what the agent saw" text: the text read from it was
    // never sent, so presenting it under that heading would be a false record of the turn.
    if (delivery === 'image') return
    let alive = true
    setLoading(true)
    setReadErr(null)
    api.attachmentExtract(path)
      .then((r) => { if (alive) { setText(r.text || ''); setRead(r.read); setUnread(r.unread) } })
      // A failed read is not "no extractable text": that sentence would describe the file.
      .catch((e) => { if (alive) setReadErr(e) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [path, delivery])
  return (
    <Modal title={name} icon={<Paperclip size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-3">
        <Button variant="ghost-accent" size="sm" onClick={() => { onOpenFile(path); onClose() }}
          className="self-start border border-outline-variant/50">
          <ExternalLink size={14} /> Open original file
        </Button>
        {delivery === 'image' ? (
          <p className="text-on-surface-var text-[0.8125rem]">The model was shown this image itself.</p>
        ) : (
        <div>
          {delivery === 'text' && !loading && !readErr && (
            <p className="mb-2 text-on-surface-var text-[0.8125rem]">
              {reason ? `${reason} ` : ''}
              {read ? 'The text read from the image was sent instead.'
                : unread === UNREAD_NO_IMAGE_MODEL ? <>No image model is set up, so only its size and format were sent. <SetUpAnImageModel /></>
                  : 'Nothing could read the image, so only its size and format were sent.'}
            </p>
          )}
          <div className="mb-1 text-on-surface-low text-[0.75rem] uppercase tracking-wide">Extracted content (what the agent saw)</div>
          {readErr ? (
            <FieldError>Couldn't read this file's text — {(readErr as Error)?.message || 'the server did not respond'}</FieldError>
          ) : loading ? (
            <div className="flex items-center gap-2 text-on-surface-low text-[0.8125rem] py-3"><Loader2 size={14} className="animate-spin" /> Extracting…</div>
          ) : text ? (
            <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap rounded-md bg-surface-low px-m py-2 font-mono text-on-surface-var text-[0.75rem] leading-relaxed">{text}</pre>
          ) : (
            <p className="text-on-surface-low text-[0.8125rem]">No extractable text content (e.g. an image with no OCR configured).</p>
          )}
        </div>
        )}
      </div>
    </Modal>
  )
}
