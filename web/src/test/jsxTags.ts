/** Each opening tag of the named JSX elements in a source, in order: from its `<` to the `>` that
 *  ends it, read brace-aware (as `pages/settings/exclusiveChoiceNamed.test.tsx`'s `tags()` reads
 *  them), so an arrow-function prop (`onClick={() => go()}`) does not end the tag, nor a `>` in a
 *  quoted value. For the source-scanning rails. */
export function tagsOf(src: string, names: string[]): { name: string; at: number; text: string }[] {
  const out: { name: string; at: number; text: string }[] = []
  const alts = names.map((n) => n.replace('.', '\\.')).join('|')
  for (const m of src.matchAll(new RegExp(`<(${alts})(?=[\\s/>])`, 'g'))) {
    let depth = 0
    let quote = ''
    for (let i = m.index! + m[0].length; i < src.length; i++) {
      const ch = src[i]
      if (quote) { if (ch === '\\') i++; else if (ch === quote) quote = ''; continue }
      if (ch === '"' || ch === "'" || (depth > 0 && ch === '`')) quote = ch
      else if (ch === '{') depth++
      else if (ch === '}') depth--
      else if (ch === '>' && depth === 0) { out.push({ name: m[1], at: m.index!, text: src.slice(m.index!, i + 1) }); break }
    }
  }
  return out
}
