import type { McpEnvEntry } from '../../lib/api'

/** The Add-tool-server form's two environment fields, as the body `PUT /api/mcp/servers/{name}`
 *  takes. Both become the server's environment; the backend keeps every value in the credential
 *  store (mcp.json holds a reference) except the variables `plainEnv` names, which stay readable in
 *  the file as settings. A name typed in both fields is a secret — the safer reading of a slip. */
export function buildMcpEnv(secretText: string, plainText: string): { env?: Record<string, string>; plainEnv?: string[] } {
  const secret = parseEnvLines(secretText)
  const plain = parseEnvLines(plainText)
  const env = { ...plain, ...secret }
  const plainEnv = Object.keys(plain).filter((name) => !(name in secret))
  return {
    env: Object.keys(env).length ? env : undefined,
    plainEnv: plainEnv.length ? plainEnv : undefined,
  }
}

/** `KEY=value` per line; a line without `=` (or starting with one) is ignored. */
export function parseEnvLines(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of text.split('\n')) {
    const i = line.indexOf('=')
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim()
  }
  return out
}

/** Stands in, in the edit form, for a value the credential store holds. The browser never has the
 *  value: a line left holding this keeps what is saved (`keepEnv`), and anything typed over it is
 *  stored instead. */
export const STORED_VALUE_MASK = '••••••••'

/** The edit form's fields, prefilled from `GET /api/mcp/servers/{name}`: a stored variable as
 *  `NAME=<mask>` in Environment (`NAME=` when nothing is saved for it), a plain one as `NAME=value`
 *  in Plain values — the same two fields, read the same way, as the Add form. */
export function envFormFields(env: McpEnvEntry[]): { secretText: string; plainText: string } {
  const secret: string[] = []
  const plain: string[] = []
  for (const e of env) {
    if (e.plain) plain.push(`${e.name}=${e.value}`)
    else secret.push(`${e.name}=${e.hasValue ? STORED_VALUE_MASK : ''}`)
  }
  return { secretText: secret.join('\n'), plainText: plain.join('\n') }
}

/** The edit form's two fields as the body `PUT /api/mcp/servers/{name}` takes. A line still holding
 *  the mask keeps its saved value (`keepEnv`); in Plain values that moves the value into the file.
 *  A deleted line removes the variable. Otherwise the Add form's rules (`buildMcpEnv`). */
export function buildMcpEdit(secretText: string, plainText: string): { env?: Record<string, string>; plainEnv?: string[]; keepEnv?: string[] } {
  const secret = parseEnvLines(secretText)
  const plain = parseEnvLines(plainText)
  const keep = new Set<string>()
  const typed = (fields: Record<string, string>) => Object.fromEntries(
    Object.entries(fields).filter(([name, value]) => {
      if (value !== STORED_VALUE_MASK) return true
      keep.add(name)
      return false
    }))
  const env = { ...typed(plain), ...typed(secret) }
  const plainEnv = Object.keys(plain).filter((name) => !(name in secret))
  return {
    env: Object.keys(env).length ? env : undefined,
    plainEnv: plainEnv.length ? plainEnv : undefined,
    keepEnv: keep.size ? [...keep] : undefined,
  }
}

/** Arguments as one line: space-separated, and an argument holding a space or a quote double-quoted
 *  (`\"` and `\\` inside), so an edit saved without touching them sends back exactly what the server
 *  had. `parseArgs` reads it back. */
export function formatArgs(args: string[]): string {
  return args
    .map((a) => (a === '' || /[\s"']/.test(a) ? `"${a.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"` : a))
    .join(' ')
}

/** The Arguments field as argv: split on whitespace, a quoted run kept whole. Inside double quotes
 *  `\"` and `\\` are escapes; single quotes are literal. A backslash elsewhere is itself, so a
 *  Windows path needs no quoting. */
export function parseArgs(text: string): string[] {
  const out: string[] = []
  let cur = ''
  let has = false
  let quote: '"' | "'" | null = null
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (quote === '"' && ch === '\\' && (text[i + 1] === '"' || text[i + 1] === '\\')) {
      cur += text[++i]
    } else if (quote) {
      if (ch === quote) quote = null
      else cur += ch
    } else if (ch === '"' || ch === "'") {
      quote = ch
      has = true
    } else if (/\s/.test(ch)) {
      if (has) out.push(cur)
      cur = ''
      has = false
    } else {
      cur += ch
      has = true
    }
  }
  if (has) out.push(cur)
  return out
}
