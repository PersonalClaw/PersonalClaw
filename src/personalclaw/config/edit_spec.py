"""ONE validator for every config write path.

The `_EDITABLE_CONFIG` PATCH handler had the only real validation in the codebase — a
typed, bounded, allowlisted mutator that emits a SEL row on every rejection. Three other
paths wrote `config.json` without it:

* `PUT /api/config/personalclaw` re-implemented bounds for three `agent.*` fields that
  `_EDITABLE_CONFIG` already declares, and silently dropped any key it did not recognise
  as long as at least one recognised key came with it. A typo'd field name returned 200.
* `PUT /api/memory/settings` had no allowlist, no SEL audit at all, and coerced rather
  than validated: `bool("false")` is `True`, so a request to turn a memory behaviour OFF
  turned it ON, and an out-of-range `push_min_confidence` was clamped to the nearest
  bound instead of being refused.
* `personalclaw config set <key> <value>` checked only that the dotted key EXISTS in
  `to_dict()`, then wrote any value — so the CLI could write `agent.max_subagents 9999`
  past the 0..16 the API enforces on the very same field.

Silently accepting a write that does something other than what was asked is worse than
refusing it: the caller has been told it succeeded, so nothing will ever look wrong. That
is the whole reason `_EDITABLE_CONFIG` rejects instead of clamping, and it is why this
module exists as ONE function rather than four dialects of nearly-the-same rules.

The rules themselves are moved here verbatim from the PATCH handler — same types, same
bounds, same messages, same SEL `resources` strings — so consolidating cannot change what
the one already-correct path accepts. Each `return _deny(msg, resources)` became
`raise ConfigValueError(msg, resources)`; the caller decides how to render it (an HTTP 400
with a SEL row, or a CLI error and exit 1).

The spec registry stays in `dashboard/handlers/core.py`: the inert-surface census parses
that module for the `_EDITABLE_CONFIG` literal to find `editable_config` entries with no
backing field, and moving the dict here would make that detector match nothing while
looking clean.

Security posture: which fields are security controls, and which way loosens one
==============================================================================

A spec may carry ``"security"``: a :class:`SecurityControl` (the field is part of the owner's
security posture, with the rule for which direction LOOSENS it and the sentence the owner
consents to) or a :class:`NotASecurityControl` (reviewed, and why it is not one). The set of
specs holding a ``SecurityControl`` IS the list of security-sensitive fields — there is no
second list — and two rules follow from being on it, decided here and nowhere else:

* :func:`app_write_refusal` — **an app-scoped caller can never write one, in either
  direction.** A path-prefix grant such as ``permissions.api: ["/api/config"]`` says nothing
  about WHICH field is written, so an app that declared it for an ordinary setting could turn
  ``agent.yolo`` on. Tightening is refused too, deliberately: on these fields it is the
  lockout and denial-of-service direction (2FA required before it is enrolled, a one-guess
  lockout, a deny-everything command pattern, a zero budget), no shipped app writes one, and
  the value-independent rule needs no read of the stored value to decide, so it cannot race
  one. An app that needs to stop things has the incident switch (``POST /api/incident``).
* :func:`unconsented_loosening` — **the owner's write that loosens one needs
  ``confirm: true``**, the JSON literal (``safety_flags.confirm_granted``). This records that
  the owner was asked, on the wire, so a UI surface added tomorrow cannot skip the question
  by not knowing the field is sensitive: it gets ``400 confirmation_required`` carrying the
  consent sentence instead. Tightening never needs it — revoking a grant is the direction a
  broken or confused client must always be able to take.

:data:`SECURITY_SECTIONS` is the rail's handle: every editable field in one of those sections
must declare ``"security"``, so a field added to ``auth``/``security``/``sandbox``/
``guardrails``/``external_access`` cannot land without someone deciding which it is.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from personalclaw.safety_flags import confirm_granted

#: A `security.egress` host entry the matcher can actually match: DNS labels, or an IPv4
#: literal (the documented homelab-webhook case — `net.guard` compares `urlparse().hostname`,
#: so a literal address is a legitimate entry). Applied AFTER lowercase/strip/trailing-dot
#: normalisation. Underscores are allowed inside a label because internal/homelab names use
#: them. An IPv6 literal cannot appear here and never could: `":" in h` rejects it at the
#: write boundary, and `hostname` yields the un-bracketed `::1`, which no bare-domain rule
#: matches — so this closes no door that was ever open. See issue 2956.
_EGRESS_HOST_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?)*$"
)

__all__ = [
    "SECURITY_SECTIONS",
    "ConfigValueError",
    "NotASecurityControl",
    "SecurityControl",
    "app_write_refusal",
    "coerce_edit_value",
    "loosens_egress",
    "loosens_toward",
    "loosens_when",
    "loosens_when_added",
    "loosens_when_changed",
    "loosens_when_longer",
    "loosens_when_raised",
    "loosens_when_removed",
    "loosens_when_shorter",
    "security_control",
    "unconsented_loosening",
]

#: The config sections that are security posture as a whole. Every editable field under one of
#: them must declare ``"security"`` (enforced by ``tests/test_security_posture_rail.py``).
#: Security controls OUTSIDE these sections (``agent.yolo``, ``agent.approval_mode``,
#: ``browse.user_browser_enabled``, …) declare themselves individually; the section rule is the
#: part a new field cannot slip past.
SECURITY_SECTIONS: frozenset[str] = frozenset(
    {"auth", "security", "sandbox", "guardrails", "external_access"}
)


@dataclass(frozen=True)
class SecurityControl:
    """A field that is part of the owner's security posture.

    ``loosens(current, new)`` says whether writing *new* over the value in effect weakens the
    control. A *current* that cannot be read as the field's type counts as loosening: a write
    whose direction cannot be proven safe is asked about, not waved through.

    ``consent`` is what the owner agrees to, in one sentence. It is shown in the consent dialog
    of any surface that did not ask its own question, so it must be TRUE of what the looser
    value does — product copy, not a description of the field.
    """

    loosens: Callable[[Any, Any], bool]
    consent: str


@dataclass(frozen=True)
class NotASecurityControl:
    """A field in a :data:`SECURITY_SECTIONS` section that loosens nothing when changed.

    The reason is required: the declaration is a reviewer's claim, and a bare marker would be
    indistinguishable from a field nobody looked at."""

    reason: str


def loosens_when(value: Any) -> Callable[[Any, Any], bool]:
    """A switch whose *value* side is the open one: writing it loosens, from anything else."""
    return lambda current, new: new == value and current != value


def loosens_when_raised(*, unlimited: float | None = None) -> Callable[[Any, Any], bool]:
    """A ceiling: a higher number loosens it, and so does *unlimited*, the value meaning none.

    From *unlimited* nothing is looser, so every write there tightens or keeps it.
    """

    def loosens(current: Any, new: Any) -> bool:
        if not _is_number(current):
            return True
        if unlimited is not None:
            if current == unlimited:
                return False
            if new == unlimited:
                return True
        return new > current

    return loosens


def loosens_when_added() -> Callable[[Any, Any], bool]:
    """An allowlist: an entry the stored list lacks is a new grant."""

    def loosens(current: Any, new: Any) -> bool:
        if not _is_str_list(current):
            return True
        return bool(set(new) - set(current))

    return loosens


def loosens_when_removed() -> Callable[[Any, Any], bool]:
    """A denylist: dropping a stored entry un-denies whatever it matched."""

    def loosens(current: Any, new: Any) -> bool:
        if not _is_str_list(current):
            return True
        return bool(set(current) - set(new))

    return loosens


def loosens_toward(*strict_to_loose: str) -> Callable[[Any, Any], bool]:
    """An ordered choice, listed from the strictest value to the loosest.

    A value outside the order, on either side, counts as loosening: a free-text field (an agent
    profile's ``approval_mode``) can hold one, and its direction cannot be proven safe.
    """
    rank = {v: i for i, v in enumerate(strict_to_loose)}

    def loosens(current: Any, new: Any) -> bool:
        if current not in rank or new not in rank:
            return True
        return rank[new] > rank[current]

    return loosens


def loosens_when_longer() -> Callable[[Any, Any], bool]:
    """A duration (``30d``/``12h``/``15m``) where longer is looser — a session lifetime."""

    def loosens(current: Any, new: Any) -> bool:
        cur = _duration_minutes(current)
        return cur is None or _duration_minutes(new) > cur  # type: ignore[operator]

    return loosens


def loosens_when_shorter() -> Callable[[Any, Any], bool]:
    """A duration where shorter is looser — a lockout that ends sooner."""

    def loosens(current: Any, new: Any) -> bool:
        cur = _duration_minutes(current)
        return cur is None or _duration_minutes(new) < cur  # type: ignore[operator]

    return loosens


def loosens_when_changed() -> Callable[[Any, Any], bool]:
    """Any change loosens — the value is a DESTINATION, and a new one is a new recipient."""
    return lambda current, new: new != current


def loosens_egress(current: Any, new: Any) -> bool:
    """The ``security.egress`` overrides: the guard reaches further when private addresses are
    allowed, a host joins the allow list (it becomes reachable even on a private address), or a
    host leaves the deny list (a deny wins over every allow)."""
    if not isinstance(current, Mapping):
        return True
    return (
        (bool(new["allow_private"]) and not bool(current.get("allow_private")))
        or loosens_when_added()(current.get("allow_hosts", []), new["allow_hosts"])
        or loosens_when_removed()(current.get("deny_hosts", []), new["deny_hosts"])
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _duration_minutes(value: Any) -> int | None:
    """``30d``/``12h``/``15m`` in minutes — the shape the ``duration`` spec type accepts."""
    if not isinstance(value, str) or not re.fullmatch(r"\d+[mhd]", value.strip()):
        return None
    text = value.strip()
    return int(text[:-1]) * {"m": 1, "h": 60, "d": 1440}[text[-1]]


def security_control(spec: Mapping[str, Any]) -> SecurityControl | None:
    """The field's :class:`SecurityControl`, or ``None`` when it is not one."""
    control = spec.get("security")
    return control if isinstance(control, SecurityControl) else None


def app_write_refusal(field: str, spec: Mapping[str, Any], app: str) -> str:
    """Why the app-scoped caller *app* may not write *field*, or ``""`` when it may.

    Value-independent on purpose (see the module docstring): the answer depends only on WHO is
    asking and WHICH field, so it is decided before the value is read or validated, and a refused
    app learns nothing about the stored value or the field's validation rules.
    """
    if not app or security_control(spec) is None:
        return ""
    return (
        f"{field} is a security setting, and an app cannot change it — only the owner can, "
        "from Settings"
    )


def unconsented_loosening(
    field: str, spec: Mapping[str, Any], *, current: Any, new: Any, body: Any
) -> str:
    """The consent sentence when writing *new* over *current* loosens *field* and *body* does
    not carry ``confirm: true``; ``""`` otherwise.

    *new* is the value AFTER :func:`coerce_edit_value`, so the direction is judged on exactly
    what would be stored. *current* is the value in effect, defaults included.
    """
    control = security_control(spec)
    if control is None or confirm_granted(body):
        return ""
    return control.consent if control.loosens(current, new) else ""


class ConfigValueError(ValueError):
    """A rejected config value.

    Carries what the PATCH handler's `_deny` needed: the caller-facing message, the SEL
    `resources` string identifying the offending field, and the status (500 only for an
    unsupported spec type, which is a bug in the registry rather than in the request).
    """

    def __init__(self, message: str, resources: str = "", status: int = 400) -> None:
        super().__init__(message)
        self.resources = resources
        self.status = status


def coerce_edit_value(path_key: str, value: Any, spec: dict) -> Any:
    """Validate *value* against *spec* and return the normalised value to write.

    Raises `ConfigValueError` if the value is not acceptable. Normalisation happens at
    this write boundary on purpose: the file must match what `load()` will read back, or
    the file carries one answer and the runtime another.
    """
    if spec["type"] == "enum":
        if value not in spec["values"]:
            raise ConfigValueError(
                f"invalid value, must be one of {spec['values']}", f"{path_key}={value}"
            )
    elif spec["type"] == "int":
        # `isinstance(True, int)` is True and `int(True)` is 1, so without this a JSON
        # `true` would quietly become the number 1 for a numeric field. The PATCH path
        # allowed that; the PUT it now shares this code with did not, and "the looser of
        # the two wins" is the wrong way to consolidate two validators.
        if value is None or isinstance(value, bool):
            raise ConfigValueError("must be an integer", f"{path_key}={value}")
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ConfigValueError("must be an integer", f"{path_key}={value}") from None
        lo, hi = spec.get("min", 0), spec.get("max", 999999)
        if value < lo or value > hi:
            raise ConfigValueError(f"must be between {lo} and {hi}", f"{path_key}={value}")
    elif spec["type"] == "bool":
        if not isinstance(value, bool):
            raise ConfigValueError("must be a boolean", f"{path_key}={value}")
    elif spec["type"] == "float":
        if value is None or isinstance(value, bool):  # see the int branch
            raise ConfigValueError("must be a number", f"{path_key}={value}")
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ConfigValueError("must be a number", f"{path_key}={value}") from None
        if not math.isfinite(value):
            raise ConfigValueError("must be a finite number", f"{path_key}={value}")
        lo, hi = spec.get("min", 0.0), spec.get("max", 999999.0)
        if value < lo or value > hi:
            raise ConfigValueError(f"must be between {lo} and {hi}", f"{path_key}={value}")
    elif spec["type"] == "duration":
        # A duration string like "30d" / "12h" / "15m". Validated with the SAME regex the
        # loader reads it back with, so a value accepted here can never be one the loader
        # then quietly replaces with a default — a write that "succeeded" while changing
        # nothing is the worst outcome for a session-lifetime field.
        if not isinstance(value, str):
            raise ConfigValueError(
                "must be a duration string like 30d, 12h or 15m", f"{path_key}={value}"
            )
        if not re.fullmatch(r"\d+[mhd]", value.strip()):
            raise ConfigValueError(
                "must be a duration like 30d, 12h or 15m (integer + m/h/d)",
                f"{path_key}={value}",
            )
        value = value.strip()
        if int(value[:-1]) <= 0:
            raise ConfigValueError("must be greater than zero", f"{path_key}={value}")
    elif spec["type"] == "str_list":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigValueError("must be a list of strings", f"{path_key}={value}")
        max_items = spec.get("max_items", 20)
        if len(value) > max_items:
            raise ConfigValueError(f"must have at most {max_items} items", f"{path_key}={value}")
        if spec.get("each_regex"):
            for v in value:
                try:
                    re.compile(v)
                except re.error as exc:
                    raise ConfigValueError(
                        f"invalid regex {v!r}: {exc}", f"{path_key}={value}"
                    ) from None
    elif spec["type"] == "str":
        if not isinstance(value, str):
            raise ConfigValueError("must be a string", f"{path_key}={value}")
        max_len = spec.get("max_len", 256)
        if len(value) > max_len:
            raise ConfigValueError(f"must be at most {max_len} characters", f"{path_key}={value}")
        if "values" in spec and value not in spec["values"]:
            raise ConfigValueError(
                f"invalid value, must be one of {spec['values']}", f"{path_key}={value}"
            )
        values_fn = spec.get("values_fn")
        if values_fn and value not in values_fn():
            raise ConfigValueError(f"invalid value for {path_key}", f"{path_key}={value}")
        # Normalise at the WRITE boundary so the file matches what load() will
        # produce — otherwise the file carries the raw value while runtime sees the
        # sanitized one: split-brain. A sanitizer only makes changes that alter no
        # meaning (surrounding whitespace, spacing); a value it could only "fix" by
        # dropping part of it, it refuses by raising `ConfigValueError` — bot_name's
        # does, for the reason this module's docstring gives.
        sanitize = spec.get("sanitize")
        if sanitize:
            value = sanitize(value)
    elif spec["type"] == "egress":
        # The operator egress overrides object: {allow_hosts:[str], deny_hosts:[str],
        # allow_private:bool}. Normalise to exactly those keys so a stray field can't be
        # smuggled into config. Hosts are bare domains/hostnames (no scheme/path).
        if not isinstance(value, dict):
            raise ConfigValueError("must be an object", f"{path_key}={value}")
        clean: dict[str, Any] = {}
        for key in ("allow_hosts", "deny_hosts"):
            hosts = value.get(key, [])
            if not isinstance(hosts, list) or not all(isinstance(h, str) for h in hosts):
                raise ConfigValueError(f"{key} must be a list of strings", f"{path_key}.{key}")
            if len(hosts) > 100:
                raise ConfigValueError(f"{key} must have at most 100 items", f"{path_key}.{key}")
            # A host entry is a bare domain/hostname — reject anything with a scheme,
            # path, or whitespace (a URL in the allow-list would be a footgun).
            #
            # It must ALSO be an entry the matcher can actually match (issue 2956). The
            # negative checks below let `*`, `*.example.com`, `""` and `"."` through with
            # 200 OK, and `net.guard.host_matches` implements exactly one rule — "a bare
            # domain covers its subdomains" — plus `if not p: continue`. So every one of
            # those entries was INERT: `deny_hosts: ["*.example.com"]` blocked nothing
            # (the bare form blocks both `example.com` and `api.example.com`), and
            # `allow_hosts: ["*"]` on an EXCLUSIVE tier refused everything while the
            # refusal counted the dead entry as `1 host(s) allowed`. This module's own
            # docstring names that failure: a write silently doing something other than
            # what was asked is worse than a refusal, because nothing will ever look wrong.
            #
            # Refused, NOT normalised. `*.example.com` -> `example.com` would be a silent
            # WIDENING on an allow-list (the bare rule also matches `example.com` itself,
            # which a glob does not), and supporting the glob in the matcher would mint a
            # second spelling for a policy that already has one — differing in precisely
            # the apex-domain case a user cares about. One dialect, and a refusal that
            # names the form that works.
            checked: list[str] = []
            for h in hosts:
                if "/" in h or ":" in h or " " in h or len(h) > 253:
                    raise ConfigValueError(
                        f"invalid host {h!r} (bare domain/hostname only)", f"{path_key}.{key}"
                    )
                # Normalise to what the matcher compares against, so the value the UI
                # echoes and `config.json` stores IS the value enforced.
                norm = h.strip().lower().rstrip(".")
                if "*" in h:
                    raise ConfigValueError(
                        f"invalid host {h!r}: wildcards are not matched — a bare domain "
                        f"already covers its subdomains, so write "
                        f"{(norm.lstrip('*.') or 'example.com')!r}",
                        f"{path_key}.{key}",
                    )
                if not _EGRESS_HOST_RE.match(norm):
                    raise ConfigValueError(
                        f"invalid host {h!r}: no host can ever match it "
                        f"(bare domain, hostname or IPv4 literal only)",
                        f"{path_key}.{key}",
                    )
                checked.append(norm)
            clean[key] = checked
        ap = value.get("allow_private", False)
        if not isinstance(ap, bool):
            raise ConfigValueError("allow_private must be a boolean", f"{path_key}.allow_private")
        clean["allow_private"] = ap
        value = clean
    elif spec["type"] == "projection_rules":
        # A list of user-taught tool-output projection rules (TokenJuice OP6 + §2.3):
        # [{name, match_regex, strategy, head?, tail?, keep?, skip?, count?}].
        # Normalise to exactly those keys; every regex must compile + each strategy
        # must be a known builtin projector. Declarative only (no code) — a bad rule
        # is rejected here, never at dispatch time.
        from personalclaw.tool_providers.projection import _PROJECTORS

        if not isinstance(value, list):
            raise ConfigValueError("must be a list", f"{path_key}={value}")
        if len(value) > 50:
            raise ConfigValueError("must have at most 50 rules", f"{path_key}")
        strategies = set(_PROJECTORS)  # log/diff/json/test/csv/code
        clean_rules: list[dict[str, object]] = []
        for i, r in enumerate(value):
            if not isinstance(r, dict):
                raise ConfigValueError("each rule must be an object", f"{path_key}[{i}]")
            name = str(r.get("name", "")).strip()[:80]
            rx = str(r.get("match_regex", "")).strip()
            strat = str(r.get("strategy", "")).strip().lower()
            if not rx:
                raise ConfigValueError("each rule needs a match_regex", f"{path_key}[{i}]")
            if len(rx) > 500:
                raise ConfigValueError("match_regex too long (max 500)", f"{path_key}[{i}]")
            try:
                re.compile(rx)
            except re.error as exc:
                raise ConfigValueError(f"invalid regex {rx!r}: {exc}", f"{path_key}[{i}]") from None
            if strat not in strategies:
                raise ConfigValueError(
                    f"strategy must be one of {sorted(strategies)}", f"{path_key}[{i}]"
                )
            clean_rule: dict[str, object] = {"name": name, "match_regex": rx, "strategy": strat}
            # Rule ops v2 (§2.3): optional declarative line operations. Each op regex
            # must compile; head/tail must be small non-negative ints. Omitted = off.
            for k in ("head", "tail"):
                raw_op = r.get(k, 0)
                # Same guard as the top-level `int` branch, and for the same reason:
                # `isinstance(True, int)` is True and `int(True)` is 1, so without this a
                # JSON `true` would quietly become "keep the last 1 line" (#2958) — the
                # exact hardening the sibling `int` branch already enforces two dozen lines
                # up in this same function.
                if isinstance(raw_op, bool):
                    raise ConfigValueError(f"{k} must be an integer", f"{path_key}[{i}]")
                try:
                    n = int(raw_op or 0)
                except (TypeError, ValueError):
                    raise ConfigValueError(f"{k} must be an integer", f"{path_key}[{i}]") from None
                if n < 0 or n > 10_000:
                    raise ConfigValueError(f"{k} must be 0..10000", f"{path_key}[{i}]")
                if n:
                    clean_rule[k] = n
            for k in ("keep", "skip", "count"):
                op_rx = str(r.get(k, "") or "").strip()
                if not op_rx:
                    continue
                if len(op_rx) > 500:
                    raise ConfigValueError(f"{k} regex too long (max 500)", f"{path_key}[{i}]")
                try:
                    re.compile(op_rx)
                except re.error as exc:
                    raise ConfigValueError(
                        f"invalid {k} regex {op_rx!r}: {exc}", f"{path_key}[{i}]"
                    ) from None
                clean_rule[k] = op_rx
            clean_rules.append(clean_rule)
        value = clean_rules
    elif spec["type"] == "https_url":
        # MOBILE-COMPANION MC-5 — `mobile.ntfy_topic_url`. A push DESTINATION, so the
        # scheme is load-bearing: an http topic would put the pinged item id on the wire in
        # the clear. `config/loader.py` keeps the field verbatim (so the settings input
        # shows what was typed) and `push.send_ntfy` fails closed, which makes this the one
        # place a user can be TOLD, rather than watching pings silently vanish.
        # Empty is legal and means "no ntfy destination".
        if not isinstance(value, str):
            raise ConfigValueError("must be a string", f"{path_key}={value}")
        value = value.strip()
        max_len = spec.get("max_len", 512)
        if len(value) > max_len:
            raise ConfigValueError(f"must be at most {max_len} characters", f"{path_key}")
        if value:
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ConfigValueError(
                    "must be a full https:// URL — a push ping must not travel in the clear",
                    f"{path_key}",
                )
    elif spec["type"] == "skill_catalogs":
        # A list of external skill-catalog sources (AGENT-PACKS §6): [{name, url, kind}].
        # Normalise to exactly those keys; a url is required and must be http(s); kind is a
        # closed set. Pure data — nothing here is fetched or executed (AP-6 registers the
        # marketplace + fetches under the CONNECTOR egress profile). A credential is never a
        # catalog field: it would ride a request log, so it goes through the credential store.
        if not isinstance(value, list):
            raise ConfigValueError("must be a list", f"{path_key}={value}")
        if len(value) > 50:
            raise ConfigValueError("must have at most 50 catalogs", f"{path_key}")
        clean_catalogs: list[dict[str, object]] = []
        for i, c in enumerate(value):
            if not isinstance(c, dict):
                raise ConfigValueError("each catalog must be an object", f"{path_key}[{i}]")
            name = str(c.get("name", "")).strip()[:80]
            url = str(c.get("url", "")).strip()
            kind = str(c.get("kind", "index")).strip().lower() or "index"
            if not url:
                raise ConfigValueError("each catalog needs a url", f"{path_key}[{i}]")
            if len(url) > 512:
                raise ConfigValueError("url too long (max 512)", f"{path_key}[{i}]")
            # Same shape as the `https_url` branch's `parsed.netloc` check (#2958): a bare
            # scheme prefix accepts `https:///topic` — no host, nothing that could ever be
            # fetched — and stores it as a configured source. Refusing here, where the
            # request still has the caller to report a 400 to, is cheaper than surfacing the
            # failure later at fetch time (or not at all).
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ConfigValueError(
                    "url must be a full http(s):// URL with a host", f"{path_key}[{i}]"
                )
            if kind not in ("index", "tap"):
                raise ConfigValueError("kind must be 'index' or 'tap'", f"{path_key}[{i}]")
            clean_catalogs.append({"name": name, "url": url, "kind": kind})
        value = clean_catalogs
    else:
        raise ConfigValueError("unsupported config type", f"{path_key}={value}", 500)

    return value
