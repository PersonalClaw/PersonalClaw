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
"""

from __future__ import annotations

import math
import re
from typing import Any

__all__ = ["ConfigValueError", "coerce_edit_value"]


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
        # produce — otherwise the file carries the raw value (e.g. markdown/brace
        # syntax in bot_name) while runtime sees the sanitized one: split-brain.
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
            for h in hosts:
                if "/" in h or ":" in h or " " in h or len(h) > 253:
                    raise ConfigValueError(
                        f"invalid host {h!r} (bare domain/hostname only)", f"{path_key}.{key}"
                    )
            clean[key] = hosts
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
                try:
                    n = int(r.get(k, 0) or 0)
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
            if not (url.startswith("https://") or url.startswith("http://")):
                raise ConfigValueError("url must be http(s)", f"{path_key}[{i}]")
            if kind not in ("index", "tap"):
                raise ConfigValueError("kind must be 'index' or 'tap'", f"{path_key}[{i}]")
            clean_catalogs.append({"name": name, "url": url, "kind": kind})
        value = clean_catalogs
    else:
        raise ConfigValueError("unsupported config type", f"{path_key}={value}", 500)

    return value
