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
