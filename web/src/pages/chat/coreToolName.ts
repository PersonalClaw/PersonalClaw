/** The gateway's own MCP server, which serves its tools to an agent CLI (`acp/mcp_servers.py`). */
const CORE_SERVER = 'personalclaw-core'

/** How each agent CLI titles a call to one of the core server's tools: claude-code
 *  `mcp__personalclaw-core__<tool>`, codex `mcp.personalclaw-core.<tool>`, kiro-cli
 *  `Running: @personalclaw-core/<tool>`. The native agent calls the tool by its bare name. */
const CORE_TITLES = [`mcp__${CORE_SERVER}__`, `mcp.${CORE_SERVER}.`, `Running: @${CORE_SERVER}/`]

/** A core tool's bare name however its runtime titles the call (`subagent_run` for each of the
 *  three titles above); any other name as it is, so another server's tool of the same name is
 *  never taken for the gateway's. */
export function coreToolName(name: string | undefined): string | undefined {
  if (!name) return name
  const title = CORE_TITLES.find((t) => name.startsWith(t))
  return title ? name.slice(title.length) : name
}
