import { useState } from 'react'
import { Clipboard, FileText } from 'lucide-react'
import { Modal } from '../../ui/Modal'
import { PASTE_MARKER_RE } from './pasteBlocks'
import { splitFileRefs } from './parseAssistant'

export interface TurnPaste { seq: number; lines: number; content: string }

/** Render a user-typed span as LITERAL text — preserving whitespace/newlines and
 *  NOT interpreting markdown or HTML (a user typing `<h2>x</h2>` or `# title`
 *  should see exactly that, not a rendered heading). Absolute file paths still
 *  become clickable chips so "open the file I mentioned" keeps working. */
function PlainUserText({ text, onFileClick }: { text: string; onFileClick?: (path: string) => void }) {
  if (!onFileClick) return <span className="whitespace-pre-wrap break-words">{text}</span>
  return (
    <span className="whitespace-pre-wrap break-words">
      {splitFileRefs(text).map((part, i) =>
        part.kind === 'file' ? (
          <button key={i} type="button" onClick={() => onFileClick(part.value)} title={`Open ${part.value}`}
            className="mx-0.5 inline-flex items-center gap-1 rounded bg-surface-high px-1 align-baseline text-[0.92em] text-primary transition-colors hover:bg-surface-highest">
            <FileText size={11} className="shrink-0" />{part.value.split('/').pop()}
          </button>
        ) : (
          <span key={i}>{part.value}</span>
        ),
      )}
    </span>
  )
}

/** An inspectable inline chip for a `[Paste #N]` marker in a sent message —
 *  click to view the full pasted content in a modal. */
export function PasteChip({ paste }: { paste: TurnPaste }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} title={`View paste #${paste.seq} (${paste.lines} lines)`}
        className="mx-0.5 inline-flex items-center gap-1 rounded-md bg-surface-high px-1.5 py-0.5 align-baseline text-[0.85em] text-primary transition-colors hover:bg-surface-highest">
        <Clipboard size={11} className="shrink-0" /> Paste #{paste.seq}
        <span className="text-on-surface-low">· {paste.lines}L</span>
      </button>
      {open && (
        <Modal title={`Paste #${paste.seq} · ${paste.lines} lines`} icon={<Clipboard size={18} className="text-primary" />} onClose={() => setOpen(false)}>
          <pre className="overflow-auto whitespace-pre-wrap rounded-md bg-surface-low px-m py-s font-mono text-on-surface-var text-[0.8125rem] leading-relaxed">{paste.content}</pre>
        </Modal>
      )}
    </>
  )
}

/** Render a USER message: prose shown as literal text (no markdown/HTML
 *  interpretation — what they typed is what they see), `[Paste #N]` markers
 *  become inspectable PasteChips, and file paths become clickable chips. */
export function MessageBody({ text, pastes, onFileClick }: { text: string; pastes?: TurnPaste[]; onFileClick?: (path: string) => void }) {
  if (!pastes || pastes.length === 0) return <PlainUserText text={text} onFileClick={onFileClick} />
  const bySeq = new Map(pastes.map((p) => [p.seq, p]))
  const parts: React.ReactNode[] = []
  let last = 0, m: RegExpExecArray | null, k = 0
  PASTE_MARKER_RE.lastIndex = 0
  while ((m = PASTE_MARKER_RE.exec(text)) !== null) {
    const seq = Number(m[1])
    const paste = bySeq.get(seq)
    if (!paste) continue
    if (m.index > last) parts.push(<PlainUserText key={`t${k}`} text={text.slice(last, m.index)} onFileClick={onFileClick} />)
    parts.push(<PasteChip key={`p${k}`} paste={paste} />)
    last = m.index + m[0].length
    k++
  }
  if (last < text.length) parts.push(<PlainUserText key={`t${k}`} text={text.slice(last)} onFileClick={onFileClick} />)
  return <div>{parts}</div>
}
