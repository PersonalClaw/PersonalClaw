# EI-1 — SandboxProvider seam + `none` provider + ResourceCeilings

**Contract (`sandbox_providers/`).** `SandboxSpec(mode, profile, ceilings)` — `mode`
is a `wrap_argv` level (auto/off/cc/strict), `profile` a ceiling profile
(tool/session_host/build/none), `ceilings` an optional explicit `ResourceCeilings`
(else `from_config`). `SandboxProvider` ABC: `name`, `display_name`, `available()`,
`wrap(spec, argv) -> SandboxHandle`. `SandboxHandle`: `exec(**kwargs) -> Process`
(async) and `cleanup()`. Six methods across the three types.

**`none` provider** composes the existing primitives: `wrap()` = `wrap_argv(argv,
spec.mode)` for OS sandbox + `ResourceCeilings` for rlimits; `handle.exec()` =
`create_subprocess_limited(*argv, profile, ceilings)`. Byte-identical to the inline
logic in `AcpProcess.spawn` today, so consuming the seam is behavior-preserving.

**PROVIDER_TYPES.** Add `"sandbox"` + `SandboxTypeHandler` (registers a `sandbox`
app into `sandbox_providers.registry`) in the SAME commit (#47 rule). `none` is a
core builtin, self-registered on `sandbox_providers` import.

**sdk facade** `sdk/sandbox.py` re-exports the ABC + data types; inert until a
cross-repo consumer exists, so regen `inert-surface-baseline.json` same commit.

**Config.** `nofile`/`max_pids`/`max_rss_mb` already exist on `SandboxConfig`
(load/to_dict wired); add `"sandbox"` to `test_config_roundtrip`'s `_SECTIONS`.
Not editability-listed (host-safety numeric, PATCH-allowlist deliberately omits).

**Spawn wiring.** `AcpProcess.spawn` resolves the provider and calls `handle.exec`.
`SubagentManager.spawn(sandbox="none")` validates + threads the name through
`get_or_create` → the ACP concurrent path → `AcpProcess`. DEVIATION: worker is
persistent-bidirectional ACP, not one-shot; `handle.exec` launches the persistent
process. Cold-start factory path inherits `none` by default.
