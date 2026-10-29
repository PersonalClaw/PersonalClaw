/** A message's id, minted here for every message the page sends, in the gateway's own format
 *  (`ids.new_id("msg")`): `msg_` and 26 Crockford base-32 characters in lower case, 48 bits of
 *  milliseconds then 80 random bits, so ids sort by when they were made.
 *
 *  The gateway runs a message once per id: a send repeated with the same id (a double submit, a
 *  request retried after a dropped connection) is answered the first send's receipt and runs
 *  nothing. So the page names the message before it sends it, keys its own row of it by the id,
 *  and sends the same id again when it sends the same message again. */

const ALPHABET = '0123456789abcdefghjkmnpqrstvwxyz'
const CHARS = 26
const RANDOM_BITS = 80n

/** Whether *value* is a message id. */
export function isMessageId(value: unknown): value is string {
  return typeof value === 'string' && /^msg_[0-9a-hjkmnp-tv-z]{26}$/.test(value)
}

/** A new message id, its time part *now* (in milliseconds). */
export function newMessageId(now: number = Date.now()): string {
  const random = crypto.getRandomValues(new Uint8Array(10))
  let value = BigInt(Math.floor(now)) << RANDOM_BITS
  for (const [i, byte] of random.entries()) value |= BigInt(byte) << BigInt(8 * (9 - i))
  let out = ''
  for (let i = CHARS - 1; i >= 0; i--) out += ALPHABET[Number((value >> BigInt(5 * i)) & 31n)]
  return `msg_${out}`
}
