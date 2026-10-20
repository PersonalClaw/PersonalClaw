"""Reply styles — how the agent writes its replies, chosen by the owner, never what it says.

A style is one of a closed set (:data:`STYLES`): ``default`` carries no instruction, and each other
style is a bundled prompt snippet (``reply-style-<id>``), so its text is editable in Settings →
Prompts. ``plain`` is what natural voice was: the same instruction, renamed.

The choice exists at three scopes, and the order between them is stated exactly once, in
:data:`REPLY_STYLE_PRECEDENCE`; :func:`resolve` is a loop over that tuple. Nothing else — no
docstring, no test, no frontend — restates it; a caller that has to explain the order reads the
tuple, and the composer shows the scope :func:`resolve` names rather than working one out.

An identity's persona (a theme's ``persona-<id>`` snippet, sent on a new chat's first turn) is a
voice too. A chosen style wins over it: while a turn resolves to a style other than ``default``, the
persona is not sent, and the persona sets the voice only when no style is chosen. :func:`preview`
reads the same resolution, so it names which of the two a turn carries.

A floor no style can move
-------------------------
A style is tone guidance and nothing else. Its text is sent as ONE bounded section, fenced by core
(``[REPLY STYLE: <title>]`` … ``[END REPLY STYLE]``), and the platform's safety rules
(``prompt_providers.runtime.safety_rules``) are sent right after the fence, by core, whatever the
style says, so the last word before the request's guidance is never the style's. A style whose text
opens or closes a style fence of its own is refused (:class:`StyleRefused`) and the turn goes
without it: otherwise text after its fake closing tag would read as outside the style section. Your
own and an app's styles are also refused at load when their text, title or description says what
the bundled styles' content rail refuses (:data:`BEHAVIOUR_PATTERNS`, the one list both read), or
when their name in the fence could break it (:func:`_refusal`). A turn with no style carries no
section and no restated rules, so it is unchanged byte for byte.

Naming: ``reply_style``, deliberately not ``voice``. ``AgentProfile.voice`` already means the
persona ("WHO the agent is") and ``voice_profiles`` means speech.

REJECTED ALTERNATIVE — a post-hoc rewriting pass
------------------------------------------------
The obvious other implementation is to let the model answer normally and then send its answer back
through a second call that rewrites it in the chosen style. That was considered and rejected, for
three reasons in increasing order of seriousness:

1. It is slower. The user waits for two round trips to read one answer, and the second cannot start
   until the first has finished streaming.
2. It costs twice. Every styled turn pays a second full model call over the whole reply, for a
   change that is purely stylistic.
3. **It can change meaning.** A rewriting pass is a model editing text it did not reason about.
   That is precisely the operation that drops a qualifier, softens a refusal into a hedge, or turns
   "this deletes the branch" into "this cleans up the branch". A style must not be able to alter a
   fact or weaken a refusal, and a second model call over the output can do both.

So a style is an instruction appended to the turn the model sees: one call, one meaning, and the
model applies the style while it composes rather than having it applied afterwards.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)


class Style(NamedTuple):
    """A reply style: its id, its name in the picker and its one-line description.

    ``source`` is ``bundled``, ``own`` (a file under the home's ``styles/``, id ``own:<name>``)
    or ``app`` (shipped by an app holding ``replyStyles``, id ``<app>:<name>``). ``app`` and
    ``by`` name the app that adds it, ``file`` the file it was read from.
    """

    id: str
    title: str
    description: str
    source: str = "bundled"
    app: str = ""
    by: str = ""
    file: str = ""


#: No style instruction: the agent writes as its own prompt says.
DEFAULT = "default"

#: Every bundled style, in the order the picker lists them.
STYLES: tuple[Style, ...] = (
    Style(DEFAULT, "Default", "No style instruction: the agent writes as its own prompt says."),
    Style(
        "plain",
        "Plain",
        "Plainer prose: the answer first, no filler openers or summary close, the shortest "
        "accurate word.",
    ),
    Style(
        "terse",
        "Terse",
        "Short and direct: the answer first, no padding, only the reasoning that matters.",
    ),
    Style(
        "conversational",
        "Conversational",
        "Fuller and warmer: full sentences, the reasoning and background, an example when it "
        "helps.",
    ),
)

STYLE_IDS: tuple[str, ...] = tuple(s.id for s in STYLES)
_TITLES: dict[str, str] = {s.id: s.title for s in STYLES}

# ─────────────────────────── the resolution order ───────────────────────────

#: The scopes that can state a reply style, MOST specific first.
#:
#: The single statement of the resolution order: :func:`resolve` walks it and returns the first
#: scope that states a style, so reordering the tuple changes the behaviour.
#:
#: * ``conversation`` — what the composer set for THIS conversation. It wins, and only here: it is
#:   stored on the session and never written to the agent, so overriding an agent here never
#:   edits that agent.
#: * ``agent`` — what the bound agent's definition carries, so a style travels with the agent.
#: * ``platform`` — the owner's default for every agent, ``agent.reply_style`` in the config (set
#:   in Settings → Agent defaults → Reply style). Always states a style, which is what makes the
#:   walk total.
REPLY_STYLE_PRECEDENCE: tuple[str, ...] = ("conversation", "agent", "platform")

#: What the platform scope states when nothing valid is stored: no instruction. A style nobody
#: asked for is one the owner cannot attribute an output change to.
PLATFORM_FLOOR: str = DEFAULT


class Resolved(NamedTuple):
    """A resolved style and the scope (a member of :data:`REPLY_STYLE_PRECEDENCE`) that decided it.

    ``source`` is returned rather than inferred because the composer shows WHICH scope is in
    charge, and the frontend must not re-derive the order to say so. ``skipped`` lists the styles
    a more specific scope chose that are not available now (an app disabled, a grant removed, a
    file deleted): the walk passes over them, and the chat says so.
    """

    style: str
    source: str
    skipped: tuple[str, ...] = ()


#: A style's own name: what follows ``own:`` or ``<app>:`` in its id, and an own style's file name.
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
#: An app's name, as the manifest allows it (kebab-case).
_APP = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
#: The namespace of your own styles. No app adds styles under it.
OWN = "own"


def normalize_choice(value: object) -> str:
    """A stored or client-sent choice as a style id, or ``""`` ("this scope states nothing").

    A bundled id, or a well-formed ``own:<name>`` / ``<app>:<name>`` id, whether or not that style
    is available right now, so a pick survives a config load while its app is off. Anything else
    (``None``, a number, a typo, the retired ``on``/``off``) states nothing, so a malformed value
    inherits instead of forcing a style.
    """
    if not isinstance(value, str):
        return ""
    word = value.strip().lower()
    if word in STYLE_IDS:
        return word
    space, sep, name = word.partition(":")
    if sep and _APP.fullmatch(space) and _NAME.fullmatch(name):
        return word
    return ""


def resolve(conversation: object = "", agent: object = "", platform: object = "") -> Resolved:
    """Resolve one turn's style by walking :data:`REPLY_STYLE_PRECEDENCE`.

    A scope whose style is not available states nothing for this turn; the style it named is
    reported in ``skipped`` (most specific first) and the walk goes on to the next scope.
    """
    stated = {
        "conversation": normalize_choice(conversation),
        "agent": normalize_choice(agent),
        "platform": normalize_choice(platform),
    }
    skipped: list[str] = []
    for source in REPLY_STYLE_PRECEDENCE:
        style = stated[source]
        if not style:
            continue
        if is_available(style):
            return Resolved(style, source, tuple(skipped))
        skipped.append(style)
    return Resolved(PLATFORM_FLOOR, "platform", tuple(skipped))


def _configured(agent_name: str) -> tuple[str, str]:
    """``(the named agent's style, the platform default)`` from one config read.

    ``("", PLATFORM_FLOOR)`` on any read failure: the floor, never a guess.
    """
    try:
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load()
    except Exception:
        logger.debug("reply style config read failed", exc_info=True)
        return "", PLATFORM_FLOOR
    profile = cfg.agents.get(str(agent_name or "").strip()) if agent_name else None
    agent = normalize_choice(getattr(profile, "reply_style", ""))
    return agent, normalize_choice(cfg.agent.reply_style) or PLATFORM_FLOOR


def agent_default(agent_name: str) -> str:
    """The style the named agent's definition carries, or ``""`` when it states none."""
    return _configured(agent_name)[0]


def platform_default() -> str:
    """The owner's default style for every agent."""
    return _configured("")[1]


def resolve_for(session: Any) -> Resolved:
    """The style *session*'s next turn resolves to: its own choice, its agent's, the platform's."""
    agent, platform = _configured(getattr(session, "agent", "") or "")
    return resolve(getattr(session, "reply_style", ""), agent, platform)


# ───────────────────── the instruction and its bounded section ────────────────────


class StyleRefused(ValueError):
    """A style's text that would reach past its own section, and so is not sent."""


#: A style fence, opening or closing, however it is spaced or cased.
_FENCE = re.compile(r"\[\s*(?:end\s+)?reply\s+style\b", re.IGNORECASE)
_CLOSE = "[END REPLY STYLE]"


def _render(name: str) -> str:
    from personalclaw.prompt_providers.runtime import render_snippet_block

    return render_snippet_block(name)


#: The most a style may hold, in bytes, measured as the file that holds it: its front matter and
#: its text (:func:`_as_file`), for your own styles and an app's alike. The bundled styles are
#: 1.1-2.3 KB; a style is tone guidance, and every styled turn carries it.
STYLE_SIZE_CAP = 4096

#: What a style may not say. A style that names tools, permissions, approvals or the sandbox is
#: reaching for what the agent may do, not for how it writes, and one that speaks of the prompt or
#: the rules around it is reaching past its section. The ONE list: your own and an app's styles are
#: refused at load when their text, title or description matches any of it (:func:`_refusal`), and
#: the bundled styles' content rail (``tests/test_reply_style.py``, ``TestStyleOnly``) reads this
#: tuple, so the two cannot drift.
BEHAVIOUR_PATTERNS: tuple[str, ...] = (
    r"\btools?\b",
    r"\bpermission",
    r"\bapprov",
    r"\bsandbox",
    r"\bsystem prompt",
    r"\bsafety rules\b",
    r"\bignore (the|any|all|previous|earlier)\b",
    r"\byou may now\b",
)
_BEHAVIOUR = re.compile("|".join(f"(?:{p})" for p in BEHAVIOUR_PATTERNS), re.IGNORECASE)


def _as_file(title: str, description: str, text: str) -> str:
    """A style as its file holds it: the front matter, then the text. What your own style is written
    as, and what the size cap measures an app's style as."""
    return f"---\ntitle: {title}\ndescription: {description}\n---\n{text}\n"


def _breaks_fence(value: str) -> bool:
    """Whether *value* holds a bracket or a line break of any kind, either of which could break the
    one line of the fence it is written into."""
    return "[" in value or "]" in value or (value != "" and value.splitlines() != [value])


def _refusal(file: str, text: str, title: str, description: str, size: int, *, by: str = "") -> str:
    """Why a style file is refused at load, naming *file*, or ``""`` when it is accepted.

    *by* is the app's name for an app's style: it is written into the fence beside the title
    (:func:`_title`), so it is held to the title's rule."""
    if size > STYLE_SIZE_CAP:
        return f"{file} is {size} bytes, over the {STYLE_SIZE_CAP}-byte (4 KB) cap for a style."
    if not text.strip():
        return f"{file} is empty: a style needs text."
    if not title.strip():
        return f"{file} has no title: a style needs one for the picker."
    named = [("title", title), ("description", description)]
    if by:
        named.append(("app's name", by))
    for label, value in named:
        if _breaks_fence(value):
            return (
                f"{file}: its {label} holds a line break or a bracket, which could break the fence."
            )
    if _FENCE.search(text):
        return f"{file} opens or closes the style section (a style fence), so it is not loaded."
    for label, value in [("text", text), *named]:
        found = _BEHAVIOUR.search(value)
        if found:
            # The match to the end of its word ("approv" says "approving").
            said = found.group(0) + re.split(r"\W", value[found.end() :], maxsplit=1)[0]
            return (
                f"{file}: its {label} says {said!r}. A style is tone guidance only: it may not "
                "name tools, permissions, approvals or the sandbox, nor speak of the prompt or "
                "the rules around it."
            )
    return ""


class _Loaded(NamedTuple):
    style: Style
    text: str


#: The styles enabled apps add, by id (``<app>:<name>``), registered by the app loader
#: (``apps.prompt_seed``) and dropped when the app is unloaded.
_APP_STYLES: dict[str, _Loaded] = {}
#: What an app shipped and was refused, by id: ``{"id", "app", "file", "reason"}``.
_APP_REFUSED: dict[str, dict[str, str]] = {}
#: The title and owner of every style this process has seen, so a style that went away is named
#: in the chat as it was named in the picker.
_KNOWN: dict[str, tuple[str, str]] = {}


def register_app_style(
    app: str, by: str, name: str, title: str, description: str, text: str, file: str
) -> str:
    """Add an app's style (the app holds ``replyStyles``); its refusal reason, ``""`` when added."""
    style_id = f"{app}:{name}"
    by = by.strip() or app
    if app == OWN or not _APP.fullmatch(app) or not _NAME.fullmatch(name):
        reason = f"{file}: {style_id!r} is not a style id an app can add."
    else:
        size = len(_as_file(title.strip(), description.strip(), text.strip()).encode("utf-8"))
        reason = _refusal(file, text, title, description, size, by=by)
    if reason:
        _APP_REFUSED[style_id] = {"id": style_id, "app": app, "file": file, "reason": reason}
        _APP_STYLES.pop(style_id, None)
        return reason
    style = Style(style_id, title.strip(), description.strip(), "app", app, by, file)
    _APP_STYLES[style_id] = _Loaded(style, text.strip())
    _APP_REFUSED.pop(style_id, None)
    _KNOWN[style_id] = (style.title, style.by)
    return ""


def refuse_app_style(app: str, name: str, file: str, reason: str) -> None:
    """Record an app style that is not loaded (the app does not hold ``replyStyles``)."""
    style_id = f"{app}:{name}"
    _APP_STYLES.pop(style_id, None)
    _APP_REFUSED[style_id] = {"id": style_id, "app": app, "file": file, "reason": reason}


def unregister_app(app: str) -> None:
    """Drop every style *app* added or had refused: it is unloaded (``apps.app_runtime.unload``,
    whether or not its manifest could be read)."""
    for table in (_APP_STYLES, _APP_REFUSED):
        for style_id in [k for k in table if k.partition(":")[0] == app]:
            del table[style_id]


def _own_dir() -> Any:
    from personalclaw.config.loader import config_dir

    return config_dir() / "styles"


def _own_path(name: str) -> Any:
    """Your style *name*'s file, ``styles/<name>.md``. Every caller has held *name* to a plain id
    (``_NAME``); ``record_path`` holds it to one path segment inside the folder as well."""
    from personalclaw.record_ids import record_path

    return record_path(_own_dir(), name, suffix=".md", kind="reply style name")


def _front_matter(raw: str) -> tuple[dict[str, str], str]:
    """``---`` front matter (``key: value`` lines) and the body after it."""
    if not raw.startswith("---\n"):
        return {}, raw
    head, sep, body = raw[4:].partition("\n---\n")
    if not sep:
        return {}, raw
    meta: dict[str, str] = {}
    for line in head.splitlines():
        key, colon, value = line.partition(":")
        if colon:
            meta[key.strip().lower()] = value.strip()
    return meta, body


def _read_own(name: str) -> tuple[_Loaded | None, dict[str, str] | None]:
    """Your style *name* and, when it is refused, why."""
    file = f"styles/{name}.md"
    try:
        raw_bytes = _own_path(name).read_bytes()
    except OSError:
        return None, None
    style_id = f"{OWN}:{name}"
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None, {"id": style_id, "file": file, "reason": f"{file} is not UTF-8 text."}
    meta, body = _front_matter(raw)
    title, description = meta.get("title", ""), meta.get("description", "")
    reason = _refusal(file, body, title, description, len(raw_bytes))
    if reason:
        return None, {"id": style_id, "file": file, "reason": reason}
    style = Style(style_id, title, description, OWN, "", "", file)
    _KNOWN[style_id] = (style.title, "")
    return _Loaded(style, body.strip()), None


def _own_names() -> list[str]:
    try:
        return sorted(
            p.stem for p in _own_dir().glob("*.md") if p.is_file() and _NAME.fullmatch(p.stem)
        )
    except OSError:
        return []


def save_own_style(name: object, *, title: object, description: object, text: object) -> Style:
    """Write your style *name* to ``styles/<name>.md`` and return it.

    Raises :class:`ValueError` naming why when the name is not a plain id, a field is not a
    string, or the file would be refused at load (:func:`_refusal`); nothing is written then.
    """
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("a style's name is lowercase letters, digits and dashes, up to 40")
    fields = {"title": title, "description": description, "text": text}
    for key, value in fields.items():
        if not isinstance(value, str):
            raise ValueError(f"'{key}' must be a string")
    title_s, desc_s, text_s = str(title).strip(), str(description).strip(), str(text).strip()
    raw = _as_file(title_s, desc_s, text_s)
    file = f"styles/{name}.md"
    reason = _refusal(file, text_s, title_s, desc_s, len(raw.encode("utf-8")))
    if reason:
        raise ValueError(reason)
    from personalclaw.atomic_write import atomic_write

    _own_dir().mkdir(parents=True, exist_ok=True)
    atomic_write(_own_path(name), raw)
    loaded, why = _read_own(name)
    if loaded is None:
        raise ValueError((why or {}).get("reason", f"{file} could not be read back"))
    return loaded.style


def delete_own_style(name: object) -> bool:
    """Remove your style *name*; False when there is no such file."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        return False
    try:
        _own_path(name).unlink()
    except FileNotFoundError:
        return False
    return True


def _lookup(style_id: str) -> _Loaded | None:
    space, _, name = style_id.partition(":")
    if space == OWN:
        return _read_own(name)[0] if _NAME.fullmatch(name) else None
    return _APP_STYLES.get(style_id)


def is_available(style: str) -> bool:
    """Whether *style* can be sent now: a bundled id, or a loaded own or app style."""
    return style in STYLE_IDS or _lookup(style) is not None


def style_by_id(style_id: str) -> Style | None:
    if style_id in STYLE_IDS:
        return next(s for s in STYLES if s.id == style_id)
    found = _lookup(style_id)
    return found.style if found else None


def available_styles() -> list[Style]:
    """Every style the picker lists: the bundled ones, then yours, then the apps'."""
    own = [loaded.style for n in _own_names() if (loaded := _read_own(n)[0])]
    apps = [_APP_STYLES[k].style for k in sorted(_APP_STYLES)]
    return [*STYLES, *own, *apps]


def refused_styles() -> list[dict[str, str]]:
    """The style files that are not loaded, each with the file and why."""
    own = [why for n in _own_names() if (why := _read_own(n)[1])]
    return [*own, *(_APP_REFUSED[k] for k in sorted(_APP_REFUSED))]


def own_text(style_id: str) -> str:
    """The text of your own or an app's style, for its editor and the list; ``""`` otherwise."""
    found = _lookup(style_id) if style_id not in STYLE_IDS else None
    return found.text if found else ""


def _title(style_id: str) -> str:
    """The style's name as its fence and the chat say it: ``Formal (Acme)`` for an app's."""
    style = style_by_id(style_id)
    if style is None:
        known = _KNOWN.get(style_id)
        return known[0] if known else style_id.partition(":")[2] or style_id
    return f"{style.title} ({style.by})" if style.source == "app" else style.title


def instruction(style: str) -> str:
    """The text *style* sends: a bundled snippet (``reply-style-<id>``), your file or an app's.
    ``""`` for ``default`` and for a style that is not available.

    Read on every call rather than cached, so an edit reaches the next turn. Raises
    :class:`StyleRefused` when a bundled snippet's text opens or closes a style fence (your own and
    an app's are refused at load instead, and so are not available).
    """
    if style == DEFAULT:
        return ""
    if style not in STYLE_IDS:
        found = _lookup(style)
        return found.text if found else ""
    text = _render(f"reply-style-{style}").strip()
    if _FENCE.search(text):
        raise StyleRefused(
            f"The {_TITLES[style]} reply style's text opens or closes the style section "
            f"(reply-style-{style} in Settings → Prompts), so it is not sent."
        )
    return text


def section(style: str) -> str:
    """What a turn under *style* carries after its message: the style's text in its fence, then
    the platform's safety rules. ``""`` for ``default`` or a style with no text.

    The same one fence for every style, bundled, yours or an app's, and the rules always after it.
    Raises :class:`StyleRefused` as :func:`instruction` does.
    """
    text = instruction(style)
    if not text:
        return ""
    from personalclaw.prompt_providers.runtime import safety_rules

    return f"[REPLY STYLE: {_title(style)}]\n{text}\n{_CLOSE}\n\n{safety_rules()}"


def notice_for(resolved: Resolved) -> str:
    """What the chat says when the style it or its agent chose is gone, or ``""``."""
    if not resolved.skipped:
        return ""
    gone = resolved.skipped[0]
    title = _title(gone)
    space = gone.partition(":")[0]
    known = _KNOWN.get(gone)
    by = (known[1] if known and known[1] else space) if space != OWN else ""
    now = "no style" if resolved.style == DEFAULT else _title(resolved.style)
    whose = f'The "{title}" reply style from {by}' if by else f'Your "{title}" reply style'
    return f"{whose} is no longer available, so this chat uses {now}."


def notice_once(session: Any, resolved: Resolved) -> str:
    """The notice the next turn of *session* posts for a style it or its agent chose that is gone
    (its app disabled or without its grant, its file deleted), or ``""``.

    Said once per style that went (``session._reply_style_noticed`` remembers it, and the chat's
    saved record keeps it, so a restart does not say it again), and again only after it came back
    and went again. The turn itself uses the next level down (:func:`resolve`).
    """
    gone = resolved.skipped[0] if resolved.skipped else ""
    said = notice_for(resolved) if gone and gone != session._reply_style_noticed else ""
    session._reply_style_noticed = gone
    return said


def appended(message: str, style: str) -> str:
    """*message* with *style*'s section after it, on every turn the style resolves to.

    Every turn, not only the first (which is what a persona does): the choice can change
    mid-conversation, and a style is the first thing a long conversation forgets. *message* is
    returned unchanged for ``default``, for a refused style and on any rendering failure: a style
    never blocks a turn.
    """
    try:
        text = section(style)
    except StyleRefused as refused:
        logger.warning("%s", refused)
        return message
    except Exception:
        logger.warning("Reply style %r could not be rendered", style, exc_info=True)
        return message
    return f"{message}\n\n{text}\n" if text else message


# ─────────────────────────────── the preview ────────────────────────────────


def preview(session: Any, persona_block: Callable[[str], str]) -> dict[str, Any]:
    """Which voice *session*'s turns carry, read from the same resolution, and its exact text.

    *persona_block* renders a theme's persona exactly as the turn appends it (``""`` for a theme
    with none). The caller hands it in because the personas belong to the chat surface, which
    this module sits below.

    ``applies`` is ``"style"``, ``"persona"`` or ``"none"``. For a style, ``instruction`` is the
    section every turn appends while the chat resolves to it. For a persona (no style is
    chosen), it is the block a new chat's first turn appends, and ``first_turn_only`` says so: a
    turn after that appends nothing for it, and the preview does not know which turn is next.
    ``refused`` says why a style's text is not sent, when it is not.
    """
    resolved = resolve_for(session)
    theme = getattr(session, "color_theme", "") or ""
    block = persona_block(theme) if theme else ""
    persona = theme if block else ""
    out: dict[str, Any] = {
        "style": resolved.style,
        "source": resolved.source,
        "persona": persona,
        "applies": "none",
        "instruction": "",
        "first_turn_only": False,
        "refused": "",
    }
    if resolved.style != DEFAULT:
        out["applies"] = "style"
        try:
            out["instruction"] = section(resolved.style)
        except StyleRefused as refused:
            out["refused"] = str(refused)
        except Exception:
            logger.debug("reply style preview render failed", exc_info=True)
        return out
    if block:
        out.update(applies="persona", instruction=block, first_turn_only=True)
    return out


# ───────────── the backfill: natural voice's stored choices become styles ─────────────


def _agent_natural_voice_style(value: object, where: str) -> str:
    """What an agent's stored natural voice means as a style: ``plain`` when it was on, else
    ``""`` (the agent states nothing, which is what an agent that had it off asked for).

    Read as an agent definition's was read before reply styles (``strict_bool``), so every
    spelling that turned it on still does. ``config.json``'s agents are read the same way, by
    decision: they were read with ``bool()``, which turned a hand-written ``"false"`` on.
    """
    from personalclaw.safety_flags import strict_bool

    return "plain" if strict_bool(value, field=f"{where} natural_voice", default=False) else ""


def _conversation_natural_voice_style(value: object) -> str:
    """What a chat's stored natural voice means as a style: ``on`` → ``plain``, ``off`` →
    ``default`` (a deliberate "no style" over its agent), anything else ``""``.

    Read as a chat's restore read it before reply styles: ``on``/``off`` in any case and spacing.
    """
    word = value.strip().lower() if isinstance(value, str) else ""
    return {"on": "plain", "off": DEFAULT}.get(word, "")


def _backfill_config() -> int:
    from personalclaw.config.transactions import mutate_config

    def change(document: dict) -> int:
        changed = 0
        agents = document.get("agents")
        for entry in agents.values() if isinstance(agents, dict) else ():
            if isinstance(entry, dict) and "natural_voice" in entry:
                style = _agent_natural_voice_style(entry.pop("natural_voice"), "config agent")
                if style and not entry.get("reply_style"):
                    entry["reply_style"] = style
                changed += 1
        return changed

    return mutate_config(change)


def _backfill_agent_definitions(home: Any) -> int:
    import json

    from personalclaw.atomic_write import atomic_write

    changed = 0
    for path in sorted((home / "agents").glob("*/agent.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "natural_voice" not in data:
            continue
        if path.is_symlink():
            # The owner's own file elsewhere: never written through, and the link never
            # replaced by a copy. Named, so its natural voice can be carried over by hand.
            logger.warning(
                "Reply style backfill left %s as it is: it is a link, and its file states "
                "natural_voice",
                path,
            )
            continue
        style = _agent_natural_voice_style(data.pop("natural_voice"), str(path))
        if style and not data.get("reply_style"):
            data["reply_style"] = style
        atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))
        changed += 1
    return changed


def _backfill_sessions(home: Any) -> int:
    """Rewrite the metadata line of each kept chat that states a natural-voice choice; every
    message after it is kept byte for byte. A linked transcript is an alias of a chat kept under
    its own name (the history lists only that file), so the link is left as it is."""
    import json

    from personalclaw.atomic_write import atomic_write

    changed = 0
    root = home / "sessions"
    for path in sorted(root.rglob("*.jsonl")) if root.is_dir() else ():
        if path.is_symlink():
            continue
        try:
            with path.open(encoding="utf-8") as fh:
                first = fh.readline()
            if '"natural_voice"' not in first:
                continue
            meta = json.loads(first)
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or meta.get("_type") != "metadata":
            continue
        if "natural_voice" not in meta:
            continue
        style = _conversation_natural_voice_style(meta.pop("natural_voice"))
        if style and not meta.get("reply_style"):
            meta["reply_style"] = style
        text = path.read_text(encoding="utf-8")
        rest = text[text.index("\n") + 1 :] if "\n" in text else ""
        atomic_write(path, json.dumps(meta) + "\n" + rest)
        changed += 1
    return changed


def _backfill_snippet(home: Any) -> int:
    """An owner's edit of the natural-voice snippet becomes their edit of the plain style; the
    shipped copy, never edited, is removed."""
    import hashlib

    import yaml

    from personalclaw.atomic_write import atomic_write

    folder = home / "prompt_snippets"
    old = folder / "natural-voice.yaml"
    if not old.is_file():
        return 0
    try:
        data = yaml.safe_load(old.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return 0
    content = str(data.get("content", "")).rstrip("\n")
    stamp = data.get("bundled_sha")
    pristine = (
        not data.get("user_owned")
        and stamp is not None
        and stamp == hashlib.sha256(content.encode("utf-8")).hexdigest()
    )
    if not pristine:
        text = content.strip()
        if text.startswith("[NATURAL VOICE]"):
            text = text.removeprefix("[NATURAL VOICE]").lstrip(" ")
        if text.endswith("[END NATURAL VOICE]"):
            text = text.removesuffix("[END NATURAL VOICE]").rstrip("\n")
        target = folder / "reply-style-plain.yaml"
        keep_theirs = False
        if target.is_file():
            try:
                existing = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                existing = {}
            keep_theirs = bool(existing.get("user_owned"))
        if not keep_theirs:
            payload = {
                "name": "reply-style-plain",
                "description": data.get("description", ""),
                "content": text,
                "tags": data.get("tags") or ["system", "bundled"],
                "user_owned": True,
            }
            if not payload["description"]:
                payload.pop("description")
            atomic_write(
                target,
                yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            )
    old.unlink()
    return 1


def backfill_home() -> dict[str, int]:
    """Turn every stored natural-voice choice in the home into a reply style, once.

    ``on`` becomes ``plain`` and ``off`` becomes ``default``, in ``config.json``'s agents, the agent
    definitions and each kept chat's metadata, each read as it was read before; a style already
    stated is never overwritten, and no file it rewrites keeps a ``natural_voice`` field. A link
    is never written through: a linked definition is left and named in the log, a linked chat is
    an alias of one rewritten under its own name. An edit of the natural-voice snippet carries
    over to the plain style. Idempotent: keyed on the data, so a second run finds nothing and
    writes nothing. Run by the gateway's boot before the config is read
    (``cli_server._boot_config``).
    """
    from personalclaw.config.loader import config_dir

    home = config_dir()
    report = {
        "config": _backfill_config(),
        "agents": _backfill_agent_definitions(home),
        "sessions": _backfill_sessions(home),
        "snippet": _backfill_snippet(home),
    }
    if any(report.values()):
        logger.info("Carried natural voice's stored choices over to reply styles: %s", report)
    return report
