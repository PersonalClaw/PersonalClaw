# MCP Tool Servers

Hands every agent the tools of the Model Context Protocol (MCP) servers you connect: one
PersonalClaw starts with a command (stdio), or one at a URL over Streamable HTTP or the older
SSE transport.

**MCP Tool Servers** is a **tool provider** that ships with PersonalClaw. The gateway runs the
servers you add under Settings → Providers or on the Tools page, and those servers' tools
reach an agent through this provider, named `mcp/<server>/<tool>`. It is built in because a
connected server is otherwise useless: without it, a server the gateway is already running
exposes nothing an agent can call.

With no server configured, it serves no tools. What a tool is taken to do comes from the
server's own annotations, never from the tool's name, and a read-only label counts only from a
server you trust on the Tools page. A tool it labels read-only then asks nobody, like every read;
every other tool asks before it acts. To stop agents using
MCP tools, remove or disable the servers, or switch this provider off on the Tools page.

## What this is

A bundled app that owns its provider code. It ships inside PersonalClaw as a self-contained
directory:

- `app.json` — the manifest (identity and the provider declaration).
- `provider.py` — the implementation, exposed via `create_mcp_provider`.

It imports PersonalClaw only through the **SDK**, exactly like a Store app:

- `personalclaw.sdk.mcp`
- `personalclaw.sdk.tool`

## Settings

Each MCP server is one instance of this provider, stored in `~/.personalclaw/mcp.json`:

| Key | Label | Notes |
|---|---|---|
| `transport` | Transport | `stdio` starts the server with a command, `http` connects to its URL over Streamable HTTP, and `sse` over the older SSE transport. |
| `command` | Command | Command to start the MCP server (stdio transport). |
| `args` | Arguments | Space-separated arguments for the command. |
| `endpoint` | URL | The server's MCP endpoint for `http` or `sse` (e.g. https://mcp.example.com/mcp). |

The card shows a command, its arguments and a URL with every credential in them masked, as
the Tools page's edit form does, and a save puts each masked value back as it was. Those
values stay in `mcp.json` as written; a token kept in a server's environment or headers is
kept in the credential store instead.

## License

MIT — see `LICENSE`.
