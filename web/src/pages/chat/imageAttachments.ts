import type { ChatImageInput } from '../../lib/api'

/** The extensions the server sends down the image path — `ocr/filetype.py`'s allowlist. A chip
 *  may only say how an image goes for a file the server will treat as one. */
export const IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff', '.webp'] as const

export function isImagePath(path: string): boolean {
  const name = (path.split('/').pop() || path).toLowerCase()
  return IMAGE_EXTENSIONS.some((ext) => name.endsWith(ext))
}

/** How ONE sent turn's images reached the model — `meta.image_delivery` on the user message. */
export interface ImageDelivery {
  byPath: Record<string, 'image' | 'text'>
  /** Why an image went as text, when one did. */
  reason?: string
}

/** The sentence under the composer's chips when attached images will go as text, or `null` when
 *  they go as images (or the answer is not in yet — a chip never guesses). `read` is what the
 *  images' own extraction got: true when their content was read, false when only their size and
 *  format could be told, `undefined` while extraction is still running. */
export function imagesAsTextNote(input: ChatImageInput | undefined, count: number, read: boolean | undefined): string | null {
  if (!input || input.accepted || count < 1) return null
  const reason = input.reason.trim()
  const lead = reason ? `${reason} ` : ''
  const them = count === 1 ? 'the image' : 'the images'
  if (read === undefined) return reason || null
  if (read) return `${lead}It gets the text read from ${them} instead.`
  return `${lead}Nothing could read ${them}, so it gets only ${count === 1 ? "the image's" : "each image's"} size and format. Choose an image-understanding model in Settings → Models to have ${them} read.`
}
