# ARTIFACTS-EVOLUTION — atomic plans

**Source plan:** [`ARTIFACTS-EVOLUTION`](../plans/ARTIFACTS-EVOLUTION.md)  
**Code:** `AE`  
**Source status:** in_progress

ARTIFACTS-EVOLUTION is 9/10 atoms done; the only remaining work is the S3 T3.2 split-view iterate panel (AE-10), which is now startable (all deps landed).

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `AE-1` | ✅ | collection field wired through model/provider/handlers/tools/api.ts (tolerant reads) | — | collection round-trips through REST + MCP tool; pre-collection artifact meta.json loads clean with collection='' |
| `AE-2` | ✅ | Server-backed list-before-save dedup: find_similar + refusal-with-hint + REST 409 | — | saving same name twice without slug/force yields a hint (tool) / 409 similar_artifact_exists (REST); no -2 slug minted on disk; source_path dedup path untouched; SEL outcome=deduped |
| `AE-3` | ✅ | Artifacts get their own top-level #/artifacts route; Files artifacts tab deleted; legacy #/files/<slug> redirect | — | Files shows only file roots; an old #/files/<slug> link lands on #/artifacts/<slug>; the relocated viewer works byte-equivalently at its new home |
| `AE-4` | ✅ | ArtifactCard live sandboxed preview grid (lazy IntersectionObserver + LRU-12 iframe cap) + 200-artifact perf proof | `AE-3` | mixed-kind library renders live theme-correct previews; off-screen cards hold placeholders; 200-artifact grid scrolls smooth (<=cap live iframes); GET /api/artifacts payload is content-free end-to-end |
| `AE-5` | ✅ | Grid toolbar: search + kind/source chips + collection picker/assign + sort, URL-query-backed | `AE-4`, `AE-1` | filter state round-trips the URL (?q/?kind/?src/?col/?sort); assigning a collection from the card/detail menu persists via PATCH |
| `AE-6` | ✅ | Full-page detail view: always-visible version picker/events, ?v=N historical deep-link, live_dirty drift badge | `AE-3` | ?v=N deep-link opens the immutable snapshot with read-only banner; drift badge matches the Files-era behavior on file-backed artifacts |
| `AE-7` | ✅ | artifact investigate resolver (agent task mode, fenced current content, slug-naming opening prompt) | `EXT:INVESTIGATE-ANYWHERE:register_investigate_resolver registry primitive` | POST /api/investigate {kind:artifact} produces a session whose first turn carries the fenced artifact content (v-current, not stale) with agent mode + #/artifacts/<slug> back-link |
| `AE-8` | ✅ | Chat @-artifact references: composer menu + meta.artifacts + _inject_artifact_content + referenced event | — | @sales-dashboard in chat grounds the reply in the current version; the artifact's events show one idempotent referenced entry with the session id; injection degrades safely if the provider raises |
| `AE-9` | ✅ | Version Compare mode: Monaco text diff (text/visual kinds) + side-by-side (binary), content-type-registry driven | `AE-6` | picking two versions (e.g. v3 vs v7) shows the correct diff per kind; offered only when >=2 versions exist; whitespace-only changes are not hidden |
| `AE-10` | ✅ | Split-view iterate panel: ChatEmbed beside the detail view; preview refreshes when artifact_update lands a new version | `AE-7`, `AE-6` | asking the agent to change the widget in the side panel produces a new version in the rail AND the preview updates without a reload (host-side useChatSocket filtered on the existing tool_call WS event where tool==='artifact_update'; no new WS event added) |

## Atom scopes

### `AE-1` — collection field wired through model/provider/handlers/tools/api.ts (tolerant reads)

**Status:** done

Session 1 T1.1; Contracts C1 (Model addition, clean break + tolerant reads)

**Done when:** collection round-trips through REST + MCP tool; pre-collection artifact meta.json loads clean with collection=''

### `AE-2` — Server-backed list-before-save dedup: find_similar + refusal-with-hint + REST 409

**Status:** done

Session 1 T1.2; Contracts C2 (Save dedup, error envelope §2.2)

**Done when:** saving same name twice without slug/force yields a hint (tool) / 409 similar_artifact_exists (REST); no -2 slug minted on disk; source_path dedup path untouched; SEL outcome=deduped

### `AE-3` — Artifacts get their own top-level #/artifacts route; Files artifacts tab deleted; legacy #/files/<slug> redirect

**Status:** done

Session 1 T1.3; Contracts C3 (Routes + library surface)

**Done when:** Files shows only file roots; an old #/files/<slug> link lands on #/artifacts/<slug>; the relocated viewer works byte-equivalently at its new home

### `AE-4` — ArtifactCard live sandboxed preview grid (lazy IntersectionObserver + LRU-12 iframe cap) + 200-artifact perf proof

**Status:** done

Session 2 T2.1 + T2.4; Contracts C3 (library surface)

**Done when:** mixed-kind library renders live theme-correct previews; off-screen cards hold placeholders; 200-artifact grid scrolls smooth (<=cap live iframes); GET /api/artifacts payload is content-free end-to-end

### `AE-5` — Grid toolbar: search + kind/source chips + collection picker/assign + sort, URL-query-backed

**Status:** done

Session 2 T2.2

**Done when:** filter state round-trips the URL (?q/?kind/?src/?col/?sort); assigning a collection from the card/detail menu persists via PATCH

### `AE-6` — Full-page detail view: always-visible version picker/events, ?v=N historical deep-link, live_dirty drift badge

**Status:** done

Session 2 T2.3

**Done when:** ?v=N deep-link opens the immutable snapshot with read-only banner; drift badge matches the Files-era behavior on file-backed artifacts

### `AE-7` — artifact investigate resolver (agent task mode, fenced current content, slug-naming opening prompt)

**Status:** done

Session 3 T3.1; Contracts C4 (consumes plan 60 registry)

**Done when:** POST /api/investigate {kind:artifact} produces a session whose first turn carries the fenced artifact content (v-current, not stale) with agent mode + #/artifacts/<slug> back-link

### `AE-8` — Chat @-artifact references: composer menu + meta.artifacts + _inject_artifact_content + referenced event

**Status:** done

Session 3 T3.4; Contracts C4 (chat side)

**Done when:** @sales-dashboard in chat grounds the reply in the current version; the artifact's events show one idempotent referenced entry with the session id; injection degrades safely if the provider raises

### `AE-9` — Version Compare mode: Monaco text diff (text/visual kinds) + side-by-side (binary), content-type-registry driven

**Status:** done

Session 3 T3.3 (S3b)

**Done when:** picking two versions (e.g. v3 vs v7) shows the correct diff per kind; offered only when >=2 versions exist; whitespace-only changes are not hidden

### `AE-10` — Split-view iterate panel: ChatEmbed beside the detail view; preview refreshes when artifact_update lands a new version

**Status:** todo

Session 3 T3.2 (the plan's recorded stop point)

**Done when:** asking the agent to change the widget in the side panel produces a new version in the rail AND the preview updates without a reload (host-side useChatSocket filtered on the existing tool_call WS event where tool==='artifact_update'; no new WS event added)

