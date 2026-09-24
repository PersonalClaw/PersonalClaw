"""``net-fetch`` — the dispatchable HTTP-egress action provider (AUTOMATION-SUBSTRATE, WF2KNO-9).

One bounded, guarded, fenced HTTP **GET**, reachable by NAME from every seam that already
dispatches an action: a workflow action node (``workflows.engine.dispatch_action``), a schedule or
event trigger, and a lifecycle hook. That is the whole atom. Before it, ``net.fetch`` was a
*library* function with library callers only — ``web_poll``, the knowledge connectors, the HF token
cascade — so a workflow template that wanted to read a page had no node to put in its spec, and
``WF2KNO-9``'s four monitor/ingest templates (market-monitor, trending-repo-digest, the dual-sink
watcher, paper-ingest) were blocked on a provider nobody owned.

``action_config``::

    {"url": "https://…",
     "max_chars": 20000}          # optional; clamped into [1, MAX_TEXT_CHARS], never above

Returns JSON on ``ActionResult.stdout``::

    {"url": …, "status": 200, "content_type": …,
     "chars": 1234, "truncated": false, "bytes_truncated": false,
     "text": "<untrusted_content …>…</untrusted_content>"}

──────────────────────────────────────────────────────────────────────────────────────────────
THE SECURITY POSTURE. Everything else here is plumbing.

**1. It goes THROUGH the chokepoint, not around it.** Every byte is pulled by
:func:`personalclaw.net.client.fetch`, which is where host classification, the private/link-local
denial, the pinned-IP dial (closing the DNS-rebind TOCTOU window), the per-hop redirect
re-evaluation, the byte cap and the SEL audit all already live. This module opens no socket and
hand-rolls no check. A provider that called ``aiohttp`` directly would inherit none of it —
which is the obligation ``web/render.py`` and ``inbound/capture_proxy.py`` spell out for the
two surfaces that genuinely cannot use ``net.fetch`` (a browser owns its own resolver; an SSE
stream cannot be buffer-capped). This one has no such excuse, so it has no such exemption.

**2. Deny-by-default.** The policy is :func:`personalclaw.net.policy.fetch_action_egress_policy`,
derived from ``FETCH_ACTION`` — ``allow_only=True`` over an EMPTY host list. An unconfigured
instance therefore reaches **nowhere**, and every refusal is visible rather than silent. Read that
profile's comment for why the reachable set is the operator's existing
``security.egress.allow_hosts`` rather than a new field beside it, and why an additive
``STRICT``-based policy would have made the allow-list decorative.

**3. A blocked host is refused before the request goes out, and the refusal lands in the SEL.**
``net.fetch`` evaluates, writes the ``egress_fetch``/``denied`` audit row, and only then raises —
no session is constructed and no packet leaves. The row's ``caller`` is ``net.fetch:fetch_action``,
so a refusal from this provider is attributable in the log without reading the URL.

**4. The response is attacker-controlled text, so it is bounded then fenced — in that order.**
Bounded twice, because the two bounds stop different things: the policy's ``max_bytes`` stops a
hostile endpoint streaming megabytes into memory, and :data:`MAX_TEXT_CHARS` stops a *legitimate*
2 MB page becoming a denial-of-wallet on the next model call. Truncation happens BEFORE
:func:`personalclaw.security.fence_untrusted`, never after: fencing appends a close marker, so
truncating a fenced string would cut that marker off and hand the next reader an unclosed fence —
the exact break the fence exists to prevent.

**5. Credentials are out of scope, so they are refused rather than handled.** A URL carrying
userinfo (``https://user:token@host/``) is refused at the boundary: injecting auth into the request
is explicitly not this provider's job, and accepting the shape would mean carrying a secret through
a code path whose whole output is destined for a model context. The URL is additionally screened
once, at that same boundary, through :func:`personalclaw.security.redact_url_userinfo`, and the
screened value is what every later composition uses — the error sentence, the JSON payload, the
fence's ``source_id``. Screening once and early is the contract: ``redact_credentials`` is NOT
idempotent over a composed ``field: value`` line, so redacting after composition garbles the field
name. The shape-based sweep is deliberately NOT run over a URL — its base64 pass would rewrite
ordinary path segments into ``[REDACTED: encoded credential]`` and leave the user reading a refusal
about a URL they cannot recognise.

**6. Response HEADERS are not returned.** Only ``Content-Type``, and only so a template can tell
that it pointed at a PNG. Handing back the full header dict would put a third party's
``Set-Cookie`` into a workflow binding and, from there, into a prompt.

──────────────────────────────────────────────────────────────────────────────────────────────
WHAT THIS DELIBERATELY DOES NOT DO, so nobody mistakes the list above for the whole of egress:

* **No POST/PUT/DELETE.** Outbound writes belong to ``webhook``, which is POST-only and lives in
  ``apps/webhook-action``. A verb that changes state on somebody else's machine is a different
  governed action with a different consent story, not a parameter on this one.
* **No run-tier narrowing.** ``SafetyProfile.egress_tier`` is not consulted, matching the nearest
  egress precedent (``browse/cdp.py``'s ``_layered_policy`` is ``egress_policy_for(BROWSE)`` and no
  more). It is NOT simply forgotten: ``egress_policy_for_profile`` UNIONs a tier's preset hosts onto
  the base, which narrows an additive base like ``SOURCE`` (``triggers/web_poll.py`` uses it
  correctly for exactly that reason) but can only WIDEN an already-exclusive one — a ``registry``
  tier would add 22 package-registry hosts to this provider's reach. Honouring only the ``off`` tier
  would be coherent, but it needs a session key this provider is not handed, so it is left to the
  atom that adds one rather than half-built here.
* **No content-type allow-list.** A template may legitimately fetch HTML, JSON, XML, CSV or plain
  text; enumerating that reliably is a mime-type war, and the bound plus the fence already contain
  the damage a surprising body can do. ``content_type`` is reported so a template can decide.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import urlparse

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.errors import AgentError

logger = logging.getLogger(__name__)

PROVIDER_NAME = "net-fetch"

#: The HARD character ceiling on the text handed to a workflow node. `max_chars` on the action
#: config may only narrow this, never raise it — a template (and therefore a planner) supplies that
#: field, so a config-supplied ceiling would be a ceiling the caller sets for itself. 20k characters
#: is roughly 5k tokens: enough for a monitor page's extracted content, small enough that a run
#: which fetches on every fire cannot quietly become the biggest line on the bill.
MAX_TEXT_CHARS = 20_000

#: The ``source_type`` stamped on the fence, so a later reader (or ``learning/hygiene.py``'s tag
#: parser) can tell WHICH class of origin this text came from rather than only that it is untrusted.
FENCE_SOURCE_TYPE = "net_fetch"

#: How the text got here, for the same reason. "a web page said this" and "THIS page said this, and
#: we truncated it on the way" are different claims.
FENCE_TRANSFORMATION = "action:http-get"


def _screen_url(raw: str) -> str:
    """The ONE boundary screen for a URL, applied once before any composition.

    Only :func:`redact_url_userinfo` — positional, idempotent, and it keeps the scheme and host so
    the result is still a URL a human recognises. See this module's docstring §5 for why the
    shape-based ``redact_credentials`` sweep is not used here.
    """
    from personalclaw.security import redact_url_userinfo

    screened, _warnings = redact_url_userinfo(raw)
    return screened


def _has_userinfo(url: str) -> bool:
    """Whether ``url`` carries userinfo, judged by the PARSER rather than by a regex.

    Deliberately a second opinion from the one :func:`_screen_url` uses: the refusal and the
    redaction disagreeing is precisely the case where a credential slips into a log, so the
    stricter of the two answers wins and the screen still runs on whatever gets through.
    """
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001 — an unparseable URL is refused by the guard anyway
        return False
    return bool(parsed.username or parsed.password)


class NetFetchActionProvider(ActionProvider):
    """Fetch one URL through the egress chokepoint and return bounded, fenced text."""

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "Fetch a URL"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        started = time.monotonic()

        raw_url = str(action_config.get("url") or "").strip()
        if not raw_url:
            return self._error(
                "net-fetch needs a `url`",
                why="the action config named no URL to fetch",
                fix='set config to {"url": "https://…"}',
                started=started,
            )
        if _has_userinfo(raw_url):
            # Screened before it is named, so the refusal cannot be the thing that logs the secret.
            return self._error(
                f"net-fetch refused {_screen_url(raw_url)} because the URL carries credentials",
                why=(
                    "the URL's userinfo field holds a username or token, and this provider never "
                    "sends credentials — a fetched body ends up in a model context"
                ),
                fix=(
                    "remove the credentials from the URL; an authenticated fetch is not something "
                    "this action can do"
                ),
                started=started,
            )
        url = _screen_url(raw_url)

        from personalclaw.guardrails.incident import incident_active

        if incident_active():
            # Refused, not failed — the same reading `browse` takes. A retry loop against a control
            # someone deliberately pulled is a storm, not a recovery.
            return ActionResult(
                success=False,
                error="incident mode is active — unattended network fetches are suspended",
                duration_ms=self._ms(started),
                agent_error=AgentError(
                    code="ERR_NET_FETCH_INCIDENT_ACTIVE",
                    what="net-fetch refused to run because incident mode is active",
                    why="incident mode suspends all unattended work",
                    fix="clear incident mode in Settings → Guardrails, then re-run",
                ),
            )

        max_chars = self._max_chars(action_config)

        from personalclaw.net.policy import fetch_action_egress_policy

        policy = fetch_action_egress_policy()
        if timeout and timeout > 0:
            # Tightest wins, the rule `egress_policy_for_profile` already applies to caps: a seam
            # that allows 10s must not be overridden by the profile's 20s.
            policy = policy.with_overrides(timeout_s=min(policy.timeout_s, float(timeout)))

        from personalclaw.net import fetch as net_fetch
        from personalclaw.net.client import EgressBlocked

        try:
            response = await net_fetch(url, policy=policy)
        except EgressBlocked as blocked:
            return self._refused(blocked, policy=policy, started=started)
        except Exception as exc:  # noqa: BLE001 — an unreachable host is a result, not a crash
            return self._error(
                f"net-fetch could not reach {url}: {type(exc).__name__}: {exc}",
                why=(
                    "the request failed before a response arrived "
                    "(DNS, TLS, connection or timeout)"
                ),
                fix="check the URL and the host's availability, then re-run",
                started=started,
                code="ERR_NET_FETCH_FAILED",
            )

        return self._to_result(response, requested_url=url, max_chars=max_chars, started=started)

    # ── plumbing ─────────────────────────────────────────────────────────────

    @staticmethod
    def _max_chars(action_config: dict[str, Any]) -> int:
        """The character bound for this call: the config's, CLAMPED into ``[1, MAX_TEXT_CHARS]``.

        Clamped rather than validated, and clamped on BOTH sides. An unparseable or absent value
        takes the ceiling (the useful default); a value above the ceiling is brought down to it
        rather than rejected, because a template asking for more text should get less text, not a
        failed run — and above all must not get more.
        """
        raw = action_config.get("max_chars")
        if raw is None:
            return MAX_TEXT_CHARS
        try:
            wanted = int(raw)
        except (TypeError, ValueError):
            return MAX_TEXT_CHARS
        return max(1, min(MAX_TEXT_CHARS, wanted))

    def _refused(self, blocked: Any, *, policy: Any, started: float) -> ActionResult:
        """The egress denial, as a sentence a user can act on.

        🪤 THIS IS A PRODUCT SURFACE. A refusal that says "egress blocked" tells a self-hoster
        nothing; one that names the host and the exact place to permit it turns a dead automation
        into a two-click fix. The guard's own ``reason`` carries the host and the reason class, and
        its ``recovery_hints`` carry the generic advice — both are preserved, and the concrete
        setting is added because the guard cannot know which surface asked.
        """
        decision = getattr(blocked, "decision", None)
        host = str(getattr(decision, "host", "") or "")
        reason = str(getattr(decision, "reason", "") or "egress blocked")
        allowed = len(getattr(policy, "allow_hosts", ()) or ())
        where = "Settings → Security → Allowed Egress Hosts (security.egress.allow_hosts)"
        if allowed:
            fix = f"add {host or 'the host'} to {where}, or point the action at a listed host"
        else:
            # The empty-list case is the DEFAULT posture, so it is the one most operators meet
            # first. Saying "no hosts are permitted yet" is the difference between reading this as
            # a bug and reading it as a setting nobody has filled in.
            fix = (
                f"no hosts are permitted for automated fetches yet — add {host or 'the host'} to "
                f"{where}"
            )
        return ActionResult(
            success=False,
            error=f"net-fetch was refused by the egress guard: {reason}",
            duration_ms=self._ms(started),
            agent_error=AgentError(
                code="ERR_NET_FETCH_EGRESS_BLOCKED",
                what=f"net-fetch did not reach {host or 'the requested host'}: {reason}",
                why=(
                    "automated fetches are limited to an operator allow-list, which is exclusive: "
                    "a host that is not on it is refused before the request is made"
                ),
                fix=fix,
                suggestions=tuple(getattr(decision, "recovery_hints", ()) or ()),
            ),
        )

    def _to_result(
        self, response: Any, *, requested_url: str, max_chars: int, started: float
    ) -> ActionResult:
        """Project the guarded response into a bounded, fenced :class:`ActionResult`."""
        from personalclaw.security import fence_untrusted

        status = int(getattr(response, "status", 0) or 0)
        headers = getattr(response, "headers", {}) or {}
        content_type = str(headers.get("Content-Type", "") or "")
        # The FINAL url is redirect-influenced, so it is third-party data too and takes the same
        # single boundary screen as the requested one.
        final_url = _screen_url(str(getattr(response, "url", "") or requested_url))

        if not 200 <= status < 300:
            return self._error(
                f"net-fetch got HTTP {status} from {final_url}",
                why="the host answered with a non-success status, so there is no body to use",
                fix=(
                    "check the URL; a 4xx is usually the wrong path and a 5xx is the host's "
                    "own fault"
                ),
                started=started,
                code="ERR_NET_FETCH_FAILED",
                stdout=json.dumps(
                    {"url": final_url, "status": status, "content_type": content_type}
                ),
            )

        text = str(getattr(response, "text", "") or "")
        truncated = len(text) > max_chars
        # BOUND, then FENCE. Reversing these two lines produces an unclosed fence — see §4.
        text = text[:max_chars]
        fenced = fence_untrusted(
            text,
            source=f"{PROVIDER_NAME}:{final_url}",
            source_type=FENCE_SOURCE_TYPE,
            source_id=final_url,
            transformation_path=FENCE_TRANSFORMATION,
        )
        return ActionResult(
            success=True,
            stdout=json.dumps(
                {
                    "url": final_url,
                    "status": status,
                    "content_type": content_type,
                    "chars": len(text),
                    # Two truncation flags, not one: `truncated` is OUR character bound and
                    # `bytes_truncated` is the policy's transfer cap. A template that sees the
                    # second knows the page was bigger than the chokepoint would carry, which is a
                    # different fact from "we trimmed it for the model".
                    "truncated": truncated,
                    "bytes_truncated": bool(getattr(response, "truncated", False)),
                    "text": fenced,
                }
            ),
            duration_ms=self._ms(started),
        )

    @staticmethod
    def _ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    def _error(
        self,
        message: str,
        *,
        why: str,
        fix: str,
        started: float,
        code: str = "ERR_NET_FETCH_CONFIG",
        stdout: str = "",
    ) -> ActionResult:
        return ActionResult(
            success=False,
            stdout=stdout,
            error=message,
            duration_ms=self._ms(started),
            agent_error=AgentError(code=code, what=message, why=why, fix=fix),
        )


def create_provider(config: dict[str, Any] | None = None) -> NetFetchActionProvider:
    """Factory, for parity with the app-manifest providers.

    ``config`` is accepted and ignored: a URL is an argument, not a setting, and a factory that
    refused the argument would be permanently unusable from the manifest path — the shape the
    generated provider reference caught the first time ``browse``'s signature was wrong.
    """
    return NetFetchActionProvider()
