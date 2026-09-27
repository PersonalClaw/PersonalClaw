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

/** `AttachmentExtract.unread` when an image needed an image model and none is set up — nothing is
 *  bound to image understanding and the chat model takes no images (`knowledge/extract.py`). */
export const UNREAD_NO_IMAGE_MODEL = 'no_image_model'

/** What the attached images' own extraction got, as the note needs it: `read` when their content
 *  was read; `noImageModel` when nothing read them because no image model is set up. `undefined`
 *  while extraction is still running. */
export interface ImagesRead { read: boolean; noImageModel: boolean }

/** The sentence under the composer's chips when attached images will go as text, or `null` when
 *  they go as images (or the answer is not in yet — a chip never guesses). When no image model is
 *  set up the caller follows the sentence with a link to Settings → Models. */
export function imagesAsTextNote(input: ChatImageInput | undefined, count: number, got: ImagesRead | undefined): string | null {
  if (!input || input.accepted || count < 1) return null
  const reason = input.reason.trim()
  const lead = reason ? `${reason} ` : ''
  const them = count === 1 ? 'the image' : 'the images'
  const sizeAndFormat = `${count === 1 ? "the image's" : "each image's"} size and format`
  if (got === undefined) return reason || null
  if (got.read) return `${lead}It gets the text read from ${them} instead.`
  if (got.noImageModel) return `${lead}No image model is set up, so it gets only ${sizeAndFormat}.`
  return `${lead}Nothing could read ${them}, so it gets only ${sizeAndFormat}.`
}
