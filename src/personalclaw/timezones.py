"""The ONE place a wall-clock timezone is resolved (#2520).

**The defect this module exists to remove, measured before writing it.** A clock trigger
authored as `{"kind": "cron", "expr": "30 8 * * *"}` with no `spec.timezone` armed to
**08:30 UTC**. On this host (`/etc/localtime -> …/zoneinfo/America/Los_Angeles`) that is
**01:30 PDT** — the product returned `1788769800.0` where an 08:30-where-the-user-is
reminder is `1788795000.0`, a **-7 h** error. Nothing warned; the trigger armed happily.
Six functions had each derived their own answer to the same question:

    triggers/arm.py:44                _trigger_tz()     spec.timezone           → UTC
    schedule.py:445                   _job_tz()         job.timezone → config   → UTC
    schedule.py:432                   get_local_tz()    config                  → UTC  (!)
    triggers/calendar.py:564          _resolve_zone()   spec → config           → server-local
    knowledge/research_reports.py:396 _report_tz()      defn.tz → get_local_tz  → UTC
    knowledge/report_schedules.py:76  _effective_tz()   defn.tz → get_local_tz  → ""   (= UTC)

They did not agree. `get_local_tz()` — a function whose *name* is "local" — returned
`('UTC', ZoneInfo('UTC'))` on a PDT host, and it is what the Triggers and Calendar pages
report as `server_tz`. `_resolve_zone()` fell through to **server-local**, so the week grid
struck a different calendar column than the fire path used — the one thing its docstring
promises it will not do. `_effective_tz()` was written *specifically* to compensate for
`arm`'s UTC default and resolved to `'UTC'` itself whenever `config.timezone` was blank,
which is the case on a stock install. Every author rediscovered the pattern, and got a
slightly different one; that divergence, not the missing default, is the real cost.

**Absent and invalid are two different facts.** An invalid zone is a typo: the author
thought about timezones and mistyped. An *absent* zone means they never thought about
timezones at all — and for a local-first personal tool whose surface is "remind me at
08:30", UTC is close to the least likely thing they meant. So:

    1. an EXPLICIT name (spec.timezone / job.timezone / defn.tz)
         → used, or REFUSED with `UnknownTimeZone` if it is not an IANA key. A typo is a
           loud error where it is authored (`arm.semantic_spec_issues` turns it into a
           create-time ERROR; `cli_setup` re-prompts), never a silent 7-hour shift.
    2. `AppConfig.timezone`
         → an *ambient default*, not an authored fact about this row, so an unusable one
           is logged and SKIPPED rather than raised. Bricking every automation over one
           bad config line is not proportionate, and the only write path for that field
           (`personalclaw setup`) already refuses a typo at the prompt.
    3. the MACHINE's zone — `TZ`, then the `/etc/localtime` symlink, then `/etc/timezone`.
    4. UTC, last resort, only when the machine zone genuinely cannot be determined. The
       doctor reports this as a WARNING naming the consequence (`zone_report`), because a
       silent UTC is exactly the footgun this module removes.

**`time.tzname` is not usable** as the machine-zone source and must not be reintroduced:
it yields abbreviations (`PDT`, `CEST`) which `ZoneInfo` rejects. The `/etc/localtime`
symlink target is the workable route on macOS and Linux. Some container images *copy*
`/etc/localtime` instead of symlinking it — then there is no name in the path, and
`/etc/timezone` (Debian/Alpine) or `TZ` is the only remaining source; when none answers,
that is the honest path to the UTC last resort.

Nothing here is cached. A resolution is a `readlink` plus (at most) one `AppConfig.load()`,
which is what `_job_tz` already cost per computation, and a cache would make a corrected
config or a changed host zone require a restart to take effect.

`tests/test_timezone_resolution_has_one_owner.py` is the rail: this module is the only
place in `src/personalclaw/` allowed to construct a `ZoneInfo`, read `/etc/localtime`, or
touch `time.tzname`. A seventh hand-rolled resolver reds it.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

#: The symlink macOS and Linux point at the host's own zone. Its *target* spells the IANA
#: key; the file's contents are compiled binary and carry no name.
LOCALTIME_LINK = Path("/etc/localtime")

#: Debian/Ubuntu/Alpine write the plain IANA name here. Consulted when `/etc/localtime` is a
#: copy rather than a symlink, which is common in container images.
TIMEZONE_FILE = Path("/etc/timezone")

#: Path components after which an `/etc/localtime` target spells the IANA key.
#: macOS: `/var/db/timezone/zoneinfo/America/Los_Angeles`.
#: Linux: `/usr/share/zoneinfo/Europe/Berlin` (some distros use `zoneinfo.default`).
_ZONEINFO_DIRS = ("zoneinfo", "zoneinfo.default")

#: Where a resolved zone came from. Reported by `resolve_zone_name` and the doctor so a
#: surface can say *why* it is showing the zone it is showing.
SOURCE_EXPLICIT = "explicit"
SOURCE_CONFIG = "config"
SOURCE_MACHINE = "machine"
SOURCE_UTC_FALLBACK = "utc-fallback"

#: The zone every host has, used only when nothing else answers.
UTC_NAME = "UTC"


class UnknownTimeZone(ValueError):
    """A non-empty timezone name that is not an IANA key.

    Raised — never swallowed — for an EXPLICIT zone, so a typo surfaces where it was
    authored instead of silently relocating a reminder. Carries the offending name so a
    caller can echo it back in a form error.
    """

    def __init__(self, name: str, *, where: str = "") -> None:
        self.name = name
        self.where = where
        prefix = f"{where}: " if where else ""
        super().__init__(
            f"{prefix}unknown timezone {name!r} — not an IANA zone name. Use a name like "
            "'America/Los_Angeles' or 'Europe/London'; abbreviations such as 'PDT' or "
            "'CEST' are not IANA zones and cannot be resolved."
        )


def is_known_zone(name: str) -> bool:
    """Whether `name` is a resolvable IANA zone. `False` for `""` — absent is not valid."""
    text = str(name or "").strip()
    if not text:
        return False
    try:
        ZoneInfo(text)
    except Exception:  # noqa: BLE001 - any zoneinfo refusal means "not a usable key"
        return False
    return True


def zone_or_raise(name: str, *, where: str = "") -> tzinfo:
    """`name` as a zone, or `UnknownTimeZone`. The authoring gate.

    `where` names the field being validated (`spec.timezone`, `config.timezone`) so the
    message points at the thing the user has to fix.
    """
    text = str(name or "").strip()
    if not text:
        raise UnknownTimeZone("", where=where)
    try:
        return ZoneInfo(text)
    except Exception as exc:  # noqa: BLE001 - normalized into the one refusal type
        raise UnknownTimeZone(text, where=where) from exc


def _iana_from_link_target(target: str) -> str:
    """The IANA key spelled by an `/etc/localtime` symlink target, or `""`.

    Handles relative targets (`../usr/share/zoneinfo/Etc/UTC`) and both spellings of the
    database directory. Returns `""` when the path names no zone — a copied (non-symlink)
    `/etc/localtime` has no name to read, which is the documented route to the last resort.
    """
    parts = [p for p in str(target or "").split("/") if p and p != "."]
    for marker in _ZONEINFO_DIRS:
        if marker in parts:
            idx = len(parts) - 1 - parts[::-1].index(marker)
            return "/".join(parts[idx + 1 :])
    return ""


def machine_zone_name() -> str:
    """The host's IANA zone name, or `""` when it genuinely cannot be determined.

    Order: `TZ` (a POSIX statement of "my zone is X", and the only source a container can
    set without touching the filesystem), the `/etc/localtime` symlink target, then
    `/etc/timezone`. Each candidate is validated through `ZoneInfo` before it is returned,
    so a `TZ=PST8PDT`-style value that happens not to be a key falls through instead of
    poisoning every schedule. Never raises.
    """
    candidates: list[tuple[str, str]] = []

    env = str(os.environ.get("TZ") or "").strip().lstrip(":")
    if env:
        candidates.append(("TZ", env))

    try:
        if LOCALTIME_LINK.is_symlink():
            # `readlink` first (one hop, the literal target), then a full `resolve()` — a chained
            # symlink, or one whose first hop is relative and short, only spells the zone after
            # the chain is walked. Both are tried because either can be the one that names it.
            for target in (os.readlink(LOCALTIME_LINK), str(LOCALTIME_LINK.resolve())):
                candidates.append(("/etc/localtime", _iana_from_link_target(target)))
    except OSError:  # unreadable /etc — the next candidate applies
        pass

    try:
        if TIMEZONE_FILE.is_file():
            candidates.append(("/etc/timezone", TIMEZONE_FILE.read_text(encoding="utf-8").strip()))
    except OSError:
        pass

    for source, name in candidates:
        if not name:
            continue
        if is_known_zone(name):
            return name
        logger.debug("machine timezone from %s is %r, which is not an IANA key", source, name)
    return ""


def _config_zone_name() -> str:
    """`AppConfig.timezone` if it is usable, else `""` — with the refusal named in the log.

    An ambient default, so an unusable value degrades to the next candidate rather than
    raising: see this module's docstring, rule 2. Imported lazily so `timezones` stays a
    leaf that `triggers/calendar.py` (a deliberately pure-decision module) can import.
    """
    try:
        from personalclaw.config.loader import AppConfig

        name = str(AppConfig.load().timezone or "").strip()
    except Exception:  # noqa: BLE001 - an unreadable config is not an error here
        return ""
    if not name:
        return ""
    if is_known_zone(name):
        return name
    logger.warning(
        "config.timezone is %r, which is not an IANA zone name — ignoring it and falling "
        "back to this machine's zone. Fix it with `personalclaw setup`.",
        name,
    )
    return ""


def resolve_zone_name(explicit: str = "") -> tuple[str, str]:
    """The zone to evaluate a wall-clock statement in, as `(iana_name, source)`.

    Raises `UnknownTimeZone` when `explicit` is non-empty and not an IANA key — that is
    the whole point of the split: an absent zone gets a sensible default, a typo'd one
    gets refused. `source` is one of the `SOURCE_*` constants.
    """
    text = str(explicit or "").strip()
    if text:
        zone_or_raise(text, where="timezone")
        return text, SOURCE_EXPLICIT
    from_config = _config_zone_name()
    if from_config:
        return from_config, SOURCE_CONFIG
    from_machine = machine_zone_name()
    if from_machine:
        return from_machine, SOURCE_MACHINE
    return UTC_NAME, SOURCE_UTC_FALLBACK


def resolve_zone(explicit: str = "") -> tzinfo:
    """`resolve_zone_name`'s answer as a `tzinfo`. The function every fire path calls.

    Returns `datetime.timezone.utc` — not `ZoneInfo("UTC")` — for the last resort, because
    a host with no tz database at all would fail to construct even `ZoneInfo("UTC")`, and
    the whole point of a last resort is that it cannot fail.
    """
    name, source = resolve_zone_name(explicit)
    if source == SOURCE_UTC_FALLBACK:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - a validated name that now fails means no tz database
        logger.warning("zone %r resolved but could not be constructed; using UTC", name)
        return timezone.utc


def local_utc_offset_hours() -> float:
    """This host's current offset from UTC, in hours, without needing an IANA name.

    The consequence of a UTC fallback is measured in hours, and the offset is knowable from
    the C library even when the zone's *name* is not (which is exactly the situation the
    fallback describes). `datetime.now().astimezone()` is the no-name route to it.
    """
    try:
        offset = datetime.now().astimezone().utcoffset()
    except Exception:  # noqa: BLE001 - a broken libc clock must not break a health report
        return 0.0
    return round(offset.total_seconds() / 3600.0, 2) if offset else 0.0


def unresolved_zone_warning() -> str:
    """The doctor's WARNING when the machine zone is undeterminable, or `""`.

    Names the CONSEQUENCE, not the condition: "timed triggers will fire at UTC, which is N
    hours off this host's local time". At a zero offset it still warns — UTC happens to be
    right at this instant but does not follow DST, so the same install is wrong for half
    the year.
    """
    _name, source = resolve_zone_name()
    if source != SOURCE_UTC_FALLBACK:
        return ""
    offset = local_utc_offset_hours()
    if offset:
        consequence = (
            f"timed triggers will fire at UTC, which is {abs(offset):g} hour(s) off this "
            f"host's local time (UTC{offset:+g})"
        )
    else:
        consequence = (
            "timed triggers will fire at UTC — the same offset as this host right now, but "
            "a UTC fallback does not follow daylight saving, so it will drift by an hour"
        )
    return (
        f"this machine's timezone could not be determined ({LOCALTIME_LINK} is not a "
        f"symlink to a zoneinfo entry, {TIMEZONE_FILE} is absent, and TZ is unset or "
        f"unusable), so {consequence}. Fix it with `personalclaw setup` or by setting "
        "`timezone` to an IANA name in config.json."
    )


def zone_report() -> dict[str, Any]:
    """Every fact a health surface needs about zone resolution. Never raises.

    `warning` is non-empty only for a real problem — the UTC last resort, or a config value
    that is not an IANA key. A correctly resolved zone produces no warning, so this cannot
    become an informational line nobody reads.
    """
    machine = machine_zone_name()
    raw_config = ""
    try:
        from personalclaw.config.loader import AppConfig

        raw_config = str(AppConfig.load().timezone or "").strip()
    except Exception:  # noqa: BLE001 - report what we could read
        raw_config = ""
    config_ok = (not raw_config) or is_known_zone(raw_config)
    try:
        name, source = resolve_zone_name()
    except Exception:  # noqa: BLE001 - resolve_zone_name only raises for an EXPLICIT name
        name, source = UTC_NAME, SOURCE_UTC_FALLBACK

    warning = unresolved_zone_warning()
    if not warning and not config_ok:
        warning = (
            f"config.timezone is {raw_config!r}, which is not an IANA zone name — it is "
            f"being ignored and schedules resolve to {name!r} instead. Fix it with "
            "`personalclaw setup`."
        )
    return {
        "resolved": name,
        "source": source,
        "config": raw_config,
        "config_ok": config_ok,
        "machine": machine,
        "utc_offset_hours": local_utc_offset_hours(),
        "warning": warning,
    }
