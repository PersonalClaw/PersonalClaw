"""Suggested folder/tag organization for untagged sessions (SESSION-MANAGEMENT T2.1).

Organizing a chat is entirely manual today: someone has to open the per-session menu and
pick a folder or a tag. Nobody does that for the fiftieth chat, so sessions accumulate
untagged and the folder/tag machinery — board columns, folder groups, the search facet —
degrades into one giant undifferentiated pile. This module proposes the organization the
user would have chosen, and stops there.

Three rules are load-bearing:

* **Deterministic first.** Three signals decide the proposal without a model: title
  keywords against the existing tag/folder vocabulary, the session's ``workspace_dir``
  basename, and channel origin. A model runs ONLY when those produce nothing usable
  (:func:`propose_for_session` with ``allow_llm=True``), because the easy cases are the
  overwhelming majority and paying a roundtrip for them would be both slow and expensive.

* **Never auto-applies.** Nothing in this module writes ``folder_id`` or ``tags``.
  :func:`propose_for_session` returns a proposal and :func:`surface_proposal` raises an
  inbox row for it; the mutation happens in :func:`apply_proposal`, which is reachable
  only from an explicit user action and which delegates to the SAME endpoints the UI's own
  folder/tag controls use. A second writer for either field is the drift this project
  keeps deleting.

* **Never nags.** A proposal the user declined must not return on the next scan. The inbox
  ``dedup_key`` covers a still-open row, and a persisted decline record
  (``entity_settings/session_organize.json``) covers the resolved ones. The key includes
  the proposed VALUE, so "put this in Research" being declined does not silence a later,
  genuinely different "tag this bug" proposal for the same session.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Modes whose sessions must never be proposed for organization. A temporary or incognito
#: chat is deliberately not part of the durable, browsable corpus that folders and tags
#: organize, and an inbox row naming one would leak its existence into a permanent surface.
RESTRICTED_MODES = frozenset({"temporary", "incognito"})

#: Ceiling on tags proposed at once. A proposal the user has to edit down is a chore, not a
#: suggestion — matching the auto-tagger's own restraint (`chat_title._AUTO_TAG_MAX_TOTAL`).
MAX_TAGS = 2

#: Minimum keyword length for a title↔vocabulary match. Two-letter words ("to", "of") match
#: everything and would make every proposal a coin flip.
_MIN_KEYWORD = 3

#: Words that carry no topic. A title match on one of these is noise, not a signal.
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "what",
        "when",
        "how",
        "why",
        "can",
        "you",
        "are",
        "was",
        "new",
        "add",
        "fix",
        "get",
        "set",
        "run",
        "use",
        "does",
        "did",
        "about",
        "into",
        "some",
        "help",
        "please",
        "chat",
        "session",
    }
)

#: Directory names that identify a checkout root rather than a project. Proposing a folder
#: called "src" or "Documents" organizes nothing.
_GENERIC_DIRS = frozenset(
    {
        "src",
        "lib",
        "tmp",
        "temp",
        "home",
        "users",
        "documents",
        "downloads",
        "desktop",
        "projects",
        "code",
        "repos",
        "workspace",
        "work",
        "dev",
    }
)

_STORE = "session_organize"


@dataclass
class OrganizeProposal:
    """A proposed organization for one session. Inert until :func:`apply_proposal`.

    ``folder_id`` is an id from the existing folder vocabulary (this never proposes
    creating a folder — a folder is a structural choice, and inventing one from a title
    keyword would grow the sidebar behind the user's back). ``tag_names`` are NAMES, not
    ids, because a proposed tag may not exist yet; :func:`apply_proposal` resolves them
    through the shared ``chat_tags.create_tag`` helper, the same path the UI uses.

    ``source`` records which signal produced it (``title`` / ``workspace`` / ``channel`` /
    ``llm``) so the chip can say why and so a regression in one heuristic is attributable.
    """

    session_key: str
    folder_id: str = ""
    folder_name: str = ""
    tag_names: list[str] = field(default_factory=list)
    source: str = ""
    reason: str = ""
    #: The chat's title, carried so the inbox row can NAME the chat it is asking about.
    #: Every builder already reads `session` (one of them matches on this very title), and
    #: `surface_proposal` receives only the proposal — so without this the row could identify
    #: the chat by nothing but its key. Empty for a genuinely untitled chat, which is the one
    #: case where the key is the only handle there is.
    session_title: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.folder_id and not self.tag_names

    def to_dict(self) -> dict[str, Any]:
        return {
            "session": self.session_key,
            "folder_id": self.folder_id,
            "folder_name": self.folder_name,
            "tags": list(self.tag_names),
            "source": self.source,
            "reason": self.reason,
            "dedup_key": dedup_key_for(self),
        }


def dedup_key_for(proposal: OrganizeProposal) -> str:
    """The idempotency key for *proposal*: session + the exact value proposed.

    Keying on the session alone would be wrong in the user's favour once and against it
    forever after: the first proposal would suppress every later one, including a
    materially better one produced after the chat's topic became clear. Keying on the
    VALUE means an identical re-proposal is silently deduped (the nag case) while a
    genuinely different proposal is allowed to surface (the new-information case).
    """
    tags = ",".join(sorted(proposal.tag_names))
    return f"session_organize:{proposal.session_key}:{proposal.folder_id}:{tags}"


# ── Decline memory (entity_settings/session_organize.json) ───────────────────────
# Fail-OPEN: a missing or corrupt store means nothing is declined. The cost of that
# failure is one extra proposal the user dismisses; the cost of failing closed is a
# feature that silently stops working and cannot be diagnosed.


def _load_store() -> dict:
    from personalclaw.providers.entity_routes import _load_entity_settings

    try:
        raw = _load_entity_settings(_STORE)
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_store(store: dict) -> None:
    from personalclaw.providers.entity_routes import _save_entity_settings

    try:
        _save_entity_settings(_STORE, store)
    except Exception:
        logger.debug("session-organize store save failed", exc_info=True)


def record_decline(proposal: OrganizeProposal, *, now: float | None = None) -> None:
    """Remember that the user declined *proposal* so it is never proposed again."""
    store = _load_store()
    declined = store.setdefault("declined", {})
    if not isinstance(declined, dict):
        declined = {}
        store["declined"] = declined
    declined[dedup_key_for(proposal)] = float(now if now is not None else time.time())
    _save_store(store)


def is_declined(proposal: OrganizeProposal) -> bool:
    """True when the user already declined this exact proposal."""
    declined = _load_store().get("declined")
    if not isinstance(declined, dict):
        return False
    return dedup_key_for(proposal) in declined


# ── "Untagged" ──────────────────────────────────────────────────────────────────


def is_unorganized(session: Any) -> bool:
    """True when *session* has neither a folder nor a tag.

    "Untagged" means BOTH are absent, not either. ``restore_recent_sessions`` already
    treats a session as organized when ``bool(meta.get("folder_id"))`` — it keeps such a
    session out of the plain-recents cutoff (``chat_persistence.py:409-415``) — and a chat
    filed in a folder is findable whether or not it also carries a tag. Proposing a tag for
    an already-filed chat would be a suggestion about a solved problem.

    Restricted sessions are never candidates regardless of their metadata.
    """
    if getattr(session, "is_restricted", False):
        return False
    if str(getattr(session, "memory_mode", "") or "") in RESTRICTED_MODES:
        return False
    if str(getattr(session, "folder_id", "") or ""):
        return False
    if list(getattr(session, "tags", None) or []):
        return False
    return True


# ── Deterministic signals ───────────────────────────────────────────────────────


def _keywords(text: str) -> list[str]:
    """Topic-bearing lowercase words in *text*, longest first.

    Longest-first because a longer word is a more specific match: a title containing both
    "api" and "deployment" should match a "deployment" tag over an "api" one.
    """
    words = [w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if w]
    keep = [w for w in words if len(w) >= _MIN_KEYWORD and w not in _STOPWORDS]
    return sorted(dict.fromkeys(keep), key=len, reverse=True)


def _match_vocabulary(words: list[str], vocab: list[dict]) -> list[dict]:
    """Vocabulary entries (folders or tags) whose name matches one of *words*.

    Matches on whole words in the entry's name, not substrings: a "go" tag must not match
    the title word "going", and an "ai" tag must not match "maintain".
    """
    hits: list[dict] = []
    for entry in vocab:
        name = str(entry.get("name") or "")
        entry_words = {w for w in re.split(r"[^a-z0-9]+", name.lower()) if w}
        if not entry_words:
            continue
        if entry_words & set(words):
            hits.append(entry)
    return hits


def _from_title(session: Any, folders: list[dict], tags: list[dict]) -> OrganizeProposal | None:
    """Signal 1 — the session title against the EXISTING folder/tag vocabulary.

    Deliberately matches only what the user already created. A title keyword is evidence
    about which of the user's own categories a chat belongs to; it is not evidence that a
    new category should exist.
    """
    words = _keywords(str(getattr(session, "title", "") or ""))
    if not words:
        return None
    folder_hits = _match_vocabulary(words, folders)
    tag_hits = [t for t in _match_vocabulary(words, tags) if not t.get("status")]
    if not folder_hits and not tag_hits:
        return None
    folder = folder_hits[0] if folder_hits else {}
    tag_names = [str(t.get("name") or "") for t in tag_hits[:MAX_TAGS] if t.get("name")]
    return OrganizeProposal(
        session_key=str(getattr(session, "key", "")),
        session_title=str(getattr(session, "title", "") or ""),
        folder_id=str(folder.get("id") or ""),
        folder_name=str(folder.get("name") or ""),
        tag_names=tag_names,
        source="title",
        reason="the title matches this folder/tag",
    )


def _from_workspace(session: Any, folders: list[dict]) -> OrganizeProposal | None:
    """Signal 2 — the session's ``workspace_dir`` basename against folder names.

    A chat rooted in a working directory is about that project, and the folder named after
    it is where its siblings already live. Folder-only: a directory name is a project, and
    projects are what folders are for. Generic checkout roots ("src", "code") are ignored.
    """
    raw = str(getattr(session, "workspace_dir", "") or "")
    if not raw:
        return None
    base = raw.rstrip("/").rsplit("/", 1)[-1]
    if not base or base.lower() in _GENERIC_DIRS or len(base) < _MIN_KEYWORD:
        return None
    hits = _match_vocabulary(_keywords(base), folders)
    if not hits:
        return None
    folder = hits[0]
    return OrganizeProposal(
        session_key=str(getattr(session, "key", "")),
        session_title=str(getattr(session, "title", "") or ""),
        folder_id=str(folder.get("id") or ""),
        folder_name=str(folder.get("name") or ""),
        source="workspace",
        reason=f"this chat works in {base}",
    )


def _from_channel(session: Any, tags: list[dict]) -> OrganizeProposal | None:
    """Signal 3 — channel origin.

    A session linked to a channel (``_channel_linked``, set by ``chat_channel.py:63``) did
    not start in the dashboard; it came in over a channel and is a different KIND of chat
    from a dashboard conversation. Tag-only, and only when a matching tag already exists —
    the channel name is provider vocabulary, and minting a tag from it would put a
    provider's name into the user's own taxonomy uninvited.
    """
    if not bool(getattr(session, "_channel_linked", False)):
        return None
    channel = str(getattr(session, "_channel_id", "") or "")
    if not channel:
        return None
    hits = [t for t in _match_vocabulary(_keywords(channel), tags) if not t.get("status")]
    if not hits:
        return None
    return OrganizeProposal(
        session_key=str(getattr(session, "key", "")),
        session_title=str(getattr(session, "title", "") or ""),
        tag_names=[str(hits[0].get("name") or "")],
        source="channel",
        reason=f"this chat came from {channel}",
    )


def deterministic_proposal(
    session: Any, folders: list[dict], tags: list[dict]
) -> OrganizeProposal | None:
    """The best proposal the three deterministic signals can make, or None.

    Order is specificity, not preference: a title that names one of the user's own
    categories is the strongest evidence; the workspace directory is next; channel origin
    is a fallback that only classifies the chat's kind. The first signal to produce
    something wins — combining them would blend two different claims into one proposal the
    user cannot evaluate.
    """
    for candidate in (
        _from_title(session, folders, tags),
        _from_workspace(session, folders),
        _from_channel(session, tags),
    ):
        if candidate is not None and not candidate.is_empty:
            return candidate
    return None


# ── The ambiguous case ──────────────────────────────────────────────────────────


def is_ambiguous(session: Any, folders: list[dict], tags: list[dict]) -> bool:
    """True when the deterministic signals produce nothing and a model could still help.

    Ambiguity is not "the heuristics were unsure" — they either match the user's
    vocabulary or they don't. It is "there IS a vocabulary to sort into, and a title that
    could be sorted, but no literal word overlap". With no folders and no tags there is
    nothing to propose at all, and with no title there is nothing to reason from, so
    neither case is worth a roundtrip.
    """
    if deterministic_proposal(session, folders, tags) is not None:
        return False
    if not folders and not tags:
        return False
    return bool(_keywords(str(getattr(session, "title", "") or "")))


def build_llm_prompt(session: Any, folders: list[dict], tags: list[dict]) -> str:
    """The prompt for the ambiguous case: pick from the existing vocabulary or say NONE.

    Closed-vocabulary on purpose. An open-ended "suggest an organization" would invent
    folders and tags, and the user's taxonomy is theirs — the model's job here is
    classification into it, not extension of it.
    """
    folder_list = ", ".join(str(f.get("name") or "") for f in folders if f.get("name"))
    tag_list = ", ".join(str(t.get("name") or "") for t in tags if t.get("name"))
    title = str(getattr(session, "title", "") or "")
    return (
        "Classify one chat into an existing organization scheme.\n\n"
        f"Chat title: {title}\n"
        f"Available folders: {folder_list or '(none)'}\n"
        f"Available tags: {tag_list or '(none)'}\n\n"
        "Reply with exactly one line:\n"
        "FOLDER: <folder name or ->  TAGS: <comma-separated tag names or ->\n"
        "Use ONLY names from the lists above. Never invent a folder or tag. "
        "If nothing listed fits, reply exactly NONE."
    )


def parse_llm_reply(
    text: str, session: Any, folders: list[dict], tags: list[dict]
) -> OrganizeProposal | None:
    """Parse a model reply into a proposal, dropping anything not in the vocabulary.

    Every name is resolved against the real folder/tag lists rather than trusted, so a
    hallucinated category cannot reach a proposal — and therefore cannot reach an accept
    click that would create it.
    """
    line = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
    if not line or line.upper().startswith("NONE"):
        return None
    folder_name = ""
    tag_part = ""
    found = re.search(r"FOLDER:\s*(.*?)\s*(?:TAGS:\s*(.*))?$", line, re.IGNORECASE)
    if found:
        folder_name = (found.group(1) or "").strip()
        tag_part = (found.group(2) or "").strip()
    by_folder = {str(f.get("name") or "").lower(): f for f in folders}
    folder = by_folder.get(folder_name.lower(), {}) if folder_name not in ("", "-") else {}
    allowed = {str(t.get("name") or "").lower(): t for t in tags if not t.get("status")}
    tag_names: list[str] = []
    for raw in tag_part.split(","):
        tag = allowed.get(raw.strip().lower())
        if tag is not None and len(tag_names) < MAX_TAGS:
            name = str(tag.get("name") or "")
            if name and name not in tag_names:
                tag_names.append(name)
    proposal = OrganizeProposal(
        session_key=str(getattr(session, "key", "")),
        session_title=str(getattr(session, "title", "") or ""),
        folder_id=str(folder.get("id") or ""),
        folder_name=str(folder.get("name") or ""),
        tag_names=tag_names,
        source="llm",
        reason="suggested from the chat's topic",
    )
    return None if proposal.is_empty else proposal


async def propose_for_session(
    state: Any, session: Any, *, allow_llm: bool = True
) -> OrganizeProposal | None:
    """The proposal for *session*, or None. **Applies nothing.**

    Deterministic signals run first and short-circuit; the model is consulted only when
    :func:`is_ambiguous` says there is a vocabulary to sort into and no literal match.
    A previously declined proposal is dropped here rather than at the surfacing boundary,
    so no caller can accidentally route around the decline memory.
    """
    if not is_unorganized(session):
        return None
    folders = [f for f in (getattr(state, "_folders", None) or []) if isinstance(f, dict)]
    tags = [t for t in (getattr(state, "_tags", None) or []) if isinstance(t, dict)]

    proposal = deterministic_proposal(session, folders, tags)
    if proposal is None and allow_llm and is_ambiguous(session, folders, tags):
        proposal = await _llm_proposal(state, session, folders, tags)
    if proposal is None or proposal.is_empty:
        return None
    if is_declined(proposal):
        logger.debug("session-organize: %s already declined", dedup_key_for(proposal))
        return None
    return proposal


async def _llm_proposal(
    state: Any, session: Any, folders: list[dict], tags: list[dict]
) -> OrganizeProposal | None:
    """Ask the background session to classify an ambiguous chat. Failure ⇒ no proposal.

    Reuses ``chat_title._stream_background_prompt``, the same shared background client the
    auto-titler uses, so this cannot occupy a user-facing session or spawn a second
    utility-prompt convention.
    """
    try:
        from personalclaw.dashboard.chat_title import _stream_background_prompt

        text = await _stream_background_prompt(state, build_llm_prompt(session, folders, tags))
    except Exception:
        logger.debug("session-organize: LLM classification failed", exc_info=True)
        return None
    return parse_llm_reply(text, session, folders, tags)


# ── Surfacing ───────────────────────────────────────────────────────────────────


def surface_proposal(state: Any, proposal: OrganizeProposal) -> str:
    """Raise the inbox row for *proposal* and return its item id.

    Routed through ``emit_attention_item`` — the only correct way to raise a durable
    request — so the row and its one notification cannot drift apart. Reuses the
    registered ``skills/proposal`` pair: this IS a proposal in the attention-path sense
    (``ItemKind.PROPOSAL``), and registering a second pair for it would give the user two
    separate "proposal" rules to keep in sync for one concept.
    """
    from personalclaw.inbox import ItemKind, emit_attention_item

    body_parts = []
    if proposal.folder_name:
        body_parts.append(f"folder “{proposal.folder_name}”")
    if proposal.tag_names:
        body_parts.append("tags " + ", ".join(f"“{t}”" for t in proposal.tag_names))
    # 🪤 This row led with the SESSION KEY — "s-3bd6196a: folder “Work” … — the title matches
    # this folder/tag". So it cited a title it never showed, and identified the chat by an
    # identifier the user has no way to connect to a conversation. The title is what a chat is
    # called everywhere else in the product, and the key stays in `refs["session"]`, which is what
    # the accept/decline endpoints and the dedup key use.
    subject = f"“{proposal.session_title}”" if proposal.session_title else proposal.session_key
    return emit_attention_item(
        state,
        source="skills",
        kind="proposal",
        item_kind=ItemKind.PROPOSAL.value,
        title="Organize an untagged chat",
        body=f"{subject}: {' + '.join(body_parts)} — {proposal.reason}",
        refs={
            "session": proposal.session_key,
            "session_organize": dedup_key_for(proposal),
            "folder_id": proposal.folder_id,
            "tags": ",".join(proposal.tag_names),
        },
        dedup_key=dedup_key_for(proposal),
    )


def resolve_inbox_item(state: Any, proposal: OrganizeProposal, status: str) -> None:
    """Move *proposal*'s inbox row to a terminal status once the user decides.

    Without this the row sits open forever after the chip was answered inline — the inbox
    would keep claiming attention for a decision already made, which is the "second
    attention store" problem the unification plan exists to end. Mirrors
    ``skills/proposals._resolve_inbox_item``: never moves an item backwards into an open
    state, which would resurrect it.
    """
    key = dedup_key_for(proposal)
    try:
        from personalclaw.inbox import InboxStore, live_store

        # The RUNNING service's store when one is up: it holds items in memory and never
        # re-reads the file, so resolving on a private instance would leave the row open in
        # the API's view and be overwritten by the service's next save.
        store = live_store(state)
        if store is None:
            store = InboxStore()
            store.load()
        changed = False
        for item in store.items.values():
            if item.refs.get("session_organize") == key and item.status != status:
                item.status = status
                changed = True
        # `save()`, not `flush()`: assigning `item.status` in place never sets the store's
        # `_dirty` flag, so `flush()` (which returns early when clean) silently persisted
        # nothing. Caught by test_accept_resolves_the_inbox_row.
        if changed:
            store.save()
    except Exception:
        logger.debug("session-organize: inbox resolution failed", exc_info=True)


# ── Accept ──────────────────────────────────────────────────────────────────────


def apply_proposal(state: Any, session: Any, proposal: OrganizeProposal) -> dict[str, Any]:
    """Apply an ACCEPTED proposal. The only mutating function in this module.

    Both writes go through the existing owners: ``folder_id`` is validated against
    ``state._folders`` exactly as ``chat_folders.api_chat_session_folder`` does, and tag
    names resolve through ``chat_tags.find_tag_by_name`` / ``create_tag`` — the same helper
    the UI's create endpoint and the auto-tagger share, so a tag created by accepting a
    proposal is indistinguishable from a hand-made one. Persistence and the sessions
    broadcast are the same pair every other session-metadata writer uses.
    """
    from personalclaw.dashboard.chat_persistence import save_session_to_history
    from personalclaw.dashboard.chat_tags import _auto_color, create_tag, find_tag_by_name

    applied: dict[str, Any] = {"folder_id": "", "tags": []}
    folders = [f for f in (getattr(state, "_folders", None) or []) if isinstance(f, dict)]
    if proposal.folder_id and any(f.get("id") == proposal.folder_id for f in folders):
        session.folder_id = proposal.folder_id
        applied["folder_id"] = proposal.folder_id

    assigned: list[str] = list(getattr(session, "tags", None) or [])
    for name in proposal.tag_names[:MAX_TAGS]:
        tag = find_tag_by_name(state, name)
        if tag is None:
            tag = create_tag(state, name, color=_auto_color(name))
        if tag is None:
            continue
        tid = str(tag.get("id") or "")
        if tid and tid not in assigned:
            assigned.append(tid)
    if assigned != list(getattr(session, "tags", None) or []):
        session.tags = assigned
    applied["tags"] = list(assigned)

    save_session_to_history(state, session, force=True)
    if hasattr(state, "push_sessions_update"):
        state.push_sessions_update()
    logger.info(
        "session-organize: applied folder=%r tags=%s to %s",
        applied["folder_id"],
        applied["tags"],
        proposal.session_key,
    )
    return applied
