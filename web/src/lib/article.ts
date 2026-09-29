/** The indefinite article for a word that is interpolated into a sentence.
 *
 *  A template that writes "a" before a word it does not know read "Choose a audio file",
 *  "Drop a image file", "an 8-cycle budget run" as "a 8-cycle", and "against a 80% floor".
 *  The article follows how the word is SAID, not how it is spelled, so the rules are about
 *  sounds: a number reads as its spoken name (eight, eleven, eighteen, eighty), an acronym as
 *  its letters (an HTML file, a PDF), and a word by its first sound (an hour, a user, a
 *  one-off). It is a heuristic over English; the cases it knows are the ones the product's
 *  own labels produce. */

/** Letters whose spoken name starts with a vowel sound: "an F1 key", "an SMS". */
const VOWEL_SOUND_LETTERS = new Set(['A', 'E', 'F', 'H', 'I', 'L', 'M', 'N', 'O', 'R', 'S', 'X'])

/** Vowel-spelled words said with a consonant first: "a user", "a one-off", "a euro" — but
 *  "an uninstalled app": un- before an n is the prefix, said with a vowel. */
const CONSONANT_SOUNDING = /^(?:uni(?!n)|use|usu|uti|ure|uro|ubiq|eu|ewe|one\b|once)/

/** Consonant-spelled words said with a vowel first: "an hour", "an honest answer". */
const VOWEL_SOUNDING = /^(?:hour|honest|honou?r|heir)/

export function indefiniteArticle(phrase: string): 'a' | 'an' {
  const word = (phrase.trim().match(/^[A-Za-z0-9]+/) ?? [''])[0]
  if (!word) return 'a'
  if (/^\d/.test(word)) {
    const digits = (word.match(/^\d+/) ?? [''])[0]
    // Said by its leading group: 8… is eight/eighty/eight hundred; a leading group of
    // two digits 11 or 18 is eleven/eighteen (11 000 is "eleven thousand", 110 is not).
    if (digits.startsWith('8')) return 'an'
    const leadingGroup = digits.length % 3 || 3
    return leadingGroup === 2 && (digits.startsWith('11') || digits.startsWith('18')) ? 'an' : 'a'
  }
  if (word.length > 1 && word === word.toUpperCase() && /[A-Z]/.test(word[0])) {
    return VOWEL_SOUND_LETTERS.has(word[0]) ? 'an' : 'a'
  }
  const lower = word.toLowerCase()
  if (/^[aeiou]/.test(lower)) return CONSONANT_SOUNDING.test(lower) ? 'a' : 'an'
  return VOWEL_SOUNDING.test(lower) ? 'an' : 'a'
}

/** *phrase* with its indefinite article: "an audio file", "a PDF", "an 80% floor". */
export function withArticle(phrase: string): string {
  return `${indefiniteArticle(phrase)} ${phrase}`
}

/** A label as a noun inside a sentence: lower-cased, except an acronym, which keeps its
 *  capitals ("New PDF", not "New pdf"). */
export function labelNoun(label: string): string {
  return /^[A-Z0-9]{2,}$/.test(label) ? label : label.toLowerCase()
}
