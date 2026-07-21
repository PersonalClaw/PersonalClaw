"""web_fetch pipeline — the layered, guarded fetch behind the `web_fetch` tool.

    ① URL-provenance gate   — the URL must have appeared in context (returned by a
                              prior web_search / web_fetch in this session). A
                              mitigation against model-fabricated-URL exfil, layered
                              with ② (not relied on alone).
    ② egress chokepoint     — net.fetch (SSRF-safe: IP-pinned, redirect-revalidated).
    ③ extract               — the shared trafilatura/nh3 core (web/extract.py).
    ④ token economy         — cap to max_tokens; offset pagination (start_index →
                              next_index) so a large page is read in chunks.

Provenance is tracked per-session in-process: web_search records the URLs it surfaced
(:func:`record_seen_urls`) and web_fetch records the page it fetched, so a follow-up
fetch of a link found mid-conversation passes the gate. The gate is advisory — every
fetch still goes through the egress guard regardless.
"""

import logging
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urlparse

from personalclaw.net import STRICT, EgressBlocked, egress_policy_for
from personalclaw.net import fetch as net_fetch
from personalclaw.net.policy import EgressPolicy
from personalclaw.web.extract import extract_main_content

logger = logging.getLogger(__name__)

# ~4 chars per token (the same heuristic the Tavily adapter uses) — token budgets are
# converted to char windows for offset pagination.
_CHARS_PER_TOKEN = 4
_DEFAULT_MAX_TOKENS = 5000

# Per-session provenance: session_key → set of canonical URLs seen this session. A
# bounded in-process record (advisory gate; the egress guard is the hard control).
_seen_by_session: dict[str, set[str]] = {}
_MAX_SEEN_PER_SESSION = 2000


def _canonical(url: str) -> str:
    """Canonicalize a URL for provenance comparison (drop fragment + trailing slash)."""
    u, _ = urldefrag((url or "").strip())
    return u.rstrip("/")


def record_seen_urls(session_key: str, urls) -> None:
    """Record URLs surfaced to a session (by web_search results / a fetched page) so a
    later web_fetch of one passes the provenance gate."""
    if not session_key:
        return
    seen = _seen_by_session.setdefault(session_key, set())
    for u in urls:
        c = _canonical(u)
        if c:
            seen.add(c)
    # Bound memory: drop oldest-insertion-order excess (sets aren't ordered, so just
    # clear when far over — provenance is best-effort, not a security boundary).
    if len(seen) > _MAX_SEEN_PER_SESSION:
        _seen_by_session[session_key] = set(list(seen)[-_MAX_SEEN_PER_SESSION:])


def url_has_provenance(session_key: str, url: str) -> bool:
    """Whether ``url`` was previously surfaced to this session."""
    return _canonical(url) in _seen_by_session.get(session_key, set())


def clear_session(session_key: str) -> None:
    """Drop a session's provenance record (session end)."""
    _seen_by_session.pop(session_key, None)


@dataclass
class FetchOutcome:
    """The result of the web_fetch pipeline (the tool maps this to a ToolResult)."""

    ok: bool
    url: str = ""
    title: str = ""
    content: str = ""
    char_count: int = 0
    total_chars: int = 0
    # The [start_char, end_char) span of `content` within the full extracted document
    # — the §5 fetch-derived citation range, so a quote can be attributed to an exact
    # offset in the source (and survives pagination).
    start_char: int = 0
    end_char: int = 0
    truncated: bool = False
    next_index: int | None = None
    extractor: str = ""
    error: str = ""
    recovery_hints: list[str] = field(default_factory=list)
    risk_level: str = "safe"


async def web_fetch(
    url: str,
    *,
    session_key: str = "",
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    start_index: int = 0,
    require_provenance: bool = True,
    render: bool = False,
    policy: EgressPolicy = STRICT,
) -> FetchOutcome:
    """Fetch + extract a URL through the guarded pipeline.

    ``require_provenance`` gates on the URL having been surfaced to ``session_key``
    first (skipped when there is no session context, or for an explicit caller that
    opts out — e.g. a user-pasted URL flow). The egress guard always applies.

    ``render`` runs the page through a headless browser (Playwright) so client-rendered
    (JS) content is captured. The egress guard is enforced before the browser navigates;
    if Playwright isn't installed it falls back to the plain HTTP fetch.
    """
    url = (url or "").strip()
    if not url:
        return FetchOutcome(
            ok=False, error="url is required", recovery_hints=["Pass a non-empty 'url'."]
        )
    # Layer the operator's Security → Network egress config (allow/deny hosts,
    # allow_private) onto the caller's profile — the Security panel's contract
    # ("Denied hosts: never reachable"; "Allowed hosts: reachable even if private")
    # must hold on the agent's primary fetch surface, not just webhooks/connectors.
    # Idempotent, so a caller that already layered is unaffected.
    policy = egress_policy_for(policy)
    scheme = (urlparse(url).scheme or "").lower()
    if scheme not in ("http", "https"):
        return FetchOutcome(
            ok=False,
            url=url,
            error="url must be http(s)",
            recovery_hints=["Provide an http or https URL."],
        )

    # ① provenance gate (advisory; only enforced when we have a session to check
    #    against, so a context-less caller / user-pasted URL isn't falsely blocked).
    if require_provenance and session_key and not url_has_provenance(session_key, url):
        return FetchOutcome(
            ok=False,
            url=url,
            risk_level="caution",
            error=(
                "url has no provenance in this session — it was not returned by a "
                "prior web_search or web_fetch"
            ),
            recovery_hints=[
                "Run web_search first and fetch a URL from its results.",
                "Only fetch URLs surfaced in the conversation, not ones constructed from memory.",
            ],
        )

    # ② fetch — either a headless-browser render (JS pages) or the SSRF-safe HTTP
    #    fetch. Both enforce the egress guard before reaching the network; render
    #    falls back to HTTP when Playwright isn't installed.
    final_url = url
    if render:
        from personalclaw.web.render import render_url

        rendered = await render_url(url, policy=policy)
        if rendered.ok:
            html_body, ctype, final_url = rendered.html, "text/html", rendered.url
        elif rendered.unavailable:
            logger.info("web_fetch render requested but Playwright unavailable; using HTTP fetch")
            render = False  # fall through to the HTTP path below
        else:
            return FetchOutcome(
                ok=False,
                url=rendered.url or url,
                error=rendered.error,
                recovery_hints=list(rendered.recovery_hints or []),
                risk_level=rendered.risk_level,
            )

    if not render:
        try:
            resp = await net_fetch(url, policy=policy)
        except EgressBlocked as exc:
            return FetchOutcome(
                ok=False,
                url=url,
                error=str(exc),
                recovery_hints=list(exc.recovery_hints),
                risk_level=exc.risk_level,
            )
        except Exception as exc:
            logger.warning("web_fetch network error for %s: %s", url, exc, exc_info=True)
            return FetchOutcome(
                ok=False,
                url=url,
                error=f"fetch failed: {exc}",
                recovery_hints=[
                    "The site may be down or slow; retry, or fetch a different source."
                ],
            )
        html_body, ctype, final_url = resp.text, resp.headers.get("Content-Type", ""), resp.url

    # ③ extract — HTML through the shared trafilatura/nh3 core; non-HTML kept as text.
    if "html" in ctype.lower():
        doc = extract_main_content(html_body, url=final_url)
        full_text, title, extractor = doc.text, doc.title, doc.extractor
    else:
        full_text, title, extractor = html_body, "", "raw"

    # A fetched page is now provenanced (a link inside it may be fetched next).
    record_seen_urls(session_key, [final_url])

    # ④ token economy — offset pagination over a char window derived from max_tokens.
    total = len(full_text)
    budget = max(1, max_tokens) * _CHARS_PER_TOKEN
    start = max(0, start_index)
    window = full_text[start : start + budget]
    end = start + len(window)
    truncated = end < total
    return FetchOutcome(
        ok=True,
        url=final_url,
        title=title,
        content=window,
        char_count=len(window),
        total_chars=total,
        start_char=start,
        end_char=end,
        truncated=truncated,
        next_index=end if truncated else None,
        extractor=extractor,
    )


# ~chars of fetched content to feed the extractor LLM (a generous single-page window;
# larger pages are still capped so the prompt stays bounded).
_EXTRACT_CONTENT_CHARS = 24000


@dataclass
class ExtractOutcome:
    """The result of web_extract (the tool maps this to a ToolResult)."""

    ok: bool
    url: str = ""
    title: str = ""
    data: dict | None = None  # the structured object the LLM extracted
    error: str = ""
    recovery_hints: list[str] = field(default_factory=list)
    risk_level: str = "safe"


async def web_extract(
    url: str,
    instructions: str,
    *,
    session_key: str = "",
    require_provenance: bool = True,
    policy: EgressPolicy = STRICT,
) -> ExtractOutcome:
    """Fetch a page (through the guarded web_fetch pipeline) and extract STRUCTURED
    data from it with an LLM, per the caller's ``instructions`` (a description of the
    fields/shape wanted). Returns a parsed JSON object.

    Reuses the existing pieces — the SSRF-safe fetch + the shared extractor for the
    page text, and the system's configured model (one_shot_completion) for the
    structured extraction — so there's no new fetch path or model wiring.
    """
    if not (instructions or "").strip():
        return ExtractOutcome(
            ok=False,
            url=url,
            error="instructions are required",
            recovery_hints=["Describe the fields / shape to extract."],
        )

    fetched = await web_fetch(
        url,
        session_key=session_key,
        max_tokens=_EXTRACT_CONTENT_CHARS // _CHARS_PER_TOKEN,
        require_provenance=require_provenance,
        policy=policy,
    )
    if not fetched.ok:
        # Surface the fetch failure verbatim (provenance/egress/network) — same contract.
        return ExtractOutcome(
            ok=False,
            url=fetched.url or url,
            error=fetched.error,
            recovery_hints=fetched.recovery_hints,
            risk_level=fetched.risk_level,
        )

    content = fetched.content[:_EXTRACT_CONTENT_CHARS]
    # Fence the fetched page body as untrusted data before it reaches the extractor
    # model. web_extract runs a ONE-SHOT completion (not the agent loop), so it does
    # NOT carry the base safety-rules snippet that tells the model <untrusted_content>
    # is data — the task-web-extract prompt states that itself. Without this, a page
    # saying "ignore your instructions, return {…}" reaches the extractor unfenced (the
    # web_fetch TOOL fences its own output, but the extract sub-LLM path is separate).
    # The extraction instruction lives in the prompt system (bundled
    # ``task-web-extract``), rendered with the field spec + page.
    from personalclaw.prompt_providers.runtime import render_use_case_prompt
    from personalclaw.security import fence_untrusted

    prompt = (
        render_use_case_prompt(
            "web_extract",
            {
                "instructions": instructions.strip(),
                "title": fetched.title or "(none)",
                "url": fetched.url,
                "content": fence_untrusted(content, source=fetched.url or "web-extract"),
            },
        )
        or ""
    )
    try:
        from personalclaw.llm_helpers import one_shot_completion, parse_llm_json

        raw = await one_shot_completion(prompt, use_case="reasoning")
    except Exception as exc:
        logger.warning("web_extract LLM call failed for %s: %s", url, exc, exc_info=True)
        return ExtractOutcome(
            ok=False,
            url=fetched.url,
            title=fetched.title,
            error=f"extraction model call failed: {exc}",
            recovery_hints=["Ensure a chat/reasoning model is configured in Settings → Models."],
        )

    data = parse_llm_json(raw)
    if data is None:
        return ExtractOutcome(
            ok=False,
            url=fetched.url,
            title=fetched.title,
            error="the model did not return a parseable JSON object",
            recovery_hints=[
                "Retry, or simplify the requested shape.",
                "web_fetch returns the raw page content if structured extraction isn't needed.",
            ],
        )
    return ExtractOutcome(ok=True, url=fetched.url, title=fetched.title, data=data)
