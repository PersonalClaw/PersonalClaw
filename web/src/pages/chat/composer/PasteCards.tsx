import { useState } from 'react'
import { AnimatePresence } from 'framer-motion'
import { Clipboard, X } from 'lucide-react'
import { IconButton } from '../../../ui/IconButton'
import { Modal } from '../../../ui/Modal'
import type { PasteBlock } from '../pasteBlocks'

/** Removable attachment cards for large pastes, shown ABOVE the composer. Each
 *  pairs with an inline `[Paste #N]` marker in the prompt (so the position is
 *  visible); ✕ removes both. Click to preview the pasted content. */
export function PasteCards({ blocks, onRemove }: { blocks: PasteBlock[]; onRemove: (seq: number) => void }) {
  const [preview, setPreview] = useState<PasteBlock | null>(null)
  if (!blocks.length) return null
  return (
    <>
      <div className="mb-2 flex flex-wrap gap-2">
        {blocks.map((b) => (
          <div key={b.id} className="group flex items-center gap-2 rounded-lg border border-outline-variant/50 bg-surface-container px-2.5 py-1.5">
            <Clipboard size={13} className="shrink-0 text-primary" />
            {/* Raw: the chip's own label opens its preview; TextLink draws accent ink, and this label keeps the chip's. */}
            <button type="button" onClick={() => setPreview(b)} className="text-left text-on-surface text-[0.8125rem] hover:underline">
              Paste #{b.seq} <span className="text-on-surface-low">· {b.lines} line{b.lines === 1 ? '' : 's'}</span>
            </button>
            <IconButton icon={X} label={`Remove paste #${b.seq}`} onClick={() => onRemove(b.seq)} size={20} iconSize={13}
              tone="danger" className="shrink-0" />
          </div>
        ))}
      </div>
      <AnimatePresence>
        {preview && (
          <Modal title={`Paste #${preview.seq} · ${preview.lines} lines`} icon={<Clipboard size={18} className="text-primary" />} onClose={() => setPreview(null)}>
            <pre className="overflow-auto whitespace-pre-wrap rounded-md bg-surface-low px-m py-s font-mono text-on-surface-var text-[0.8125rem] leading-relaxed">{preview.content}</pre>
          </Modal>
        )}
      </AnimatePresence>
    </>
  )
}
