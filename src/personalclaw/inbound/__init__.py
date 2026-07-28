"""Inbound surfaces — curated, read-only ways IN (MCP-READONLY-INBOUND).

Everything else in PersonalClaw is outbound: the agent reaches out to models,
tools, and channels. This package is the opposite direction — a way for an
external client (your IDE's MCP integration, an inspector) to ask PersonalClaw
questions. That direction is dangerous by default, so the whole package is built
fail-CLOSED:

* **Off unless explicitly turned on.** A missing or corrupt `inbound.mcp.enabled`
  reads DISABLED. "Inbound off" is the safe state, so an unreadable config must
  land there rather than defaulting to on. Stated here so nobody later "fixes" the
  parse to be lenient.
* **No token, no mount.** The surface refuses to mount without a dedicated token
  of its own — and refuses one that equals the dashboard token or the internal
  secret, because reusing either would silently widen those credentials' blast
  radius to a new network surface.
* **Loopback only** unless the owner explicitly opts into remote AND declares the
  public URL. Forwarded headers are never trusted for that decision (a local port
  forwarder makes remote traffic look like 127.0.0.1, which is exactly why the
  dashboard's own middleware refuses to treat loopback as authentication).
* **Read-only by construction.** Write façades are not imported at all — the
  surface can't call a mutation it has no reference to.
* **Every result is fenced.** Results pass through one wrapper that applies
  `fence_untrusted`, so a new tool physically cannot skip the data-not-instructions
  treatment.

Session 1 (this slice) ships the substrate: auth, caps, audit, and the JSON-RPC
transport with an empty tool table. Session 2 adds the five curated tools.
"""
