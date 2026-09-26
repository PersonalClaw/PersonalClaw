"""Places outside the PersonalClaw home, each read only once the owner has allowed it.

PersonalClaw keeps what it reads and writes in its home
(:func:`~personalclaw.config.loader.config_dir`). A few features are worth more with something that
already lives elsewhere on the machine: the skills other AI tools share in ``~/.agents/skills``,
the Hugging Face folder other tools filled, another CLI's sign-in. Each such place is declared here,
is off until the owner turns it on in Settings → Security → Outside PersonalClaw's home, turns off
the same way, and is only ever READ: PersonalClaw installs, writes and deletes in its own home.

Code asks :func:`place_path` (or :func:`allowed`) for a place, and a place the owner has not allowed
reads as absent. ``tests/test_personalclaw_stays_inside_its_home.py`` fails on code that names a
location in the user's real home anywhere else, so a new outside place is declared here or not at
all.

A provider app's subscription sign-in (``llm/subscription_credentials.py``) is a place too, one per
registered source: the app declares where its CLI keeps the sign-in, and the owner allows reading it
here, per source.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: The skills folder AI tools share (agentskills.io's cross-client location).
AGENT_SKILLS = "agent-skills"
#: The machine-wide Hugging Face folder: its model cache and ``huggingface-cli login``'s token.
HUGGINGFACE_CACHE = "huggingface-cache"
#: The prefix of a subscription provider's sign-in place: ``sign-in:<source id>``.
SIGN_IN_PREFIX = "sign-in:"


@dataclass(frozen=True)
class Place:
    """One place outside the home, as Settings lists it."""

    id: str
    label: str
    #: Where it is, expanded for this machine; the first is where PersonalClaw looks first.
    paths: tuple[str, ...]
    #: What turning it on does, in one or two sentences.
    detail: str

    def to_dict(self, *, allowed: bool) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "paths": list(self.paths),
            "detail": self.detail,
            "allowed": allowed,
        }


def huggingface_home() -> Path:
    """The machine-wide Hugging Face folder, found the way ``huggingface_hub`` finds it."""
    explicit = os.environ.get("HF_HOME", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser() / "huggingface"
    return Path.home() / ".cache" / "huggingface"


def _agent_skills_dir() -> Path:
    return Path.home() / ".agents" / "skills"


def _expanded(raw: str) -> str:
    return os.path.expanduser(os.path.expandvars(raw))


def places() -> list[Place]:
    """Every place the owner can allow, core's first and then each subscription sign-in."""
    out = [
        Place(
            id=AGENT_SKILLS,
            label="Skills other AI tools share",
            paths=(str(_agent_skills_dir()),),
            detail=(
                "Your agents can also use the skills in this folder, and the Skills page lists "
                "them. PersonalClaw installs skills into its own home and never changes or "
                "deletes anything here."
            ),
        ),
        Place(
            id=HUGGINGFACE_CACHE,
            label="The Hugging Face folder other tools share",
            paths=(str(huggingface_home()),),
            detail=(
                "Model apps can use models already downloaded here, and the token saved by "
                "huggingface-cli login is used when a model needs one. PersonalClaw still "
                "downloads into its own home and never deletes anything here."
            ),
        ),
    ]
    from personalclaw.llm.subscription_credentials import registered_sources

    for source in registered_sources():
        # A candidate naming an unset variable cannot be opened, so it is not a place to show.
        paths = tuple(e for e in (_expanded(p) for p in source.credential_files) if "$" not in e)
        out.append(
            Place(
                id=SIGN_IN_PREFIX + source.id,
                label=f"The {source.id} sign-in",
                paths=paths,
                detail=(
                    f"The {source.id} provider uses this sign-in instead of an API key. "
                    "PersonalClaw only reads it, and never refreshes, changes or copies it."
                ),
            )
        )
    return out


def allowed_ids() -> set[str]:
    """The places the owner allowed (``security.outside_home``). Unreadable config allows none."""
    try:
        from personalclaw.config.loader import AppConfig

        return set(AppConfig.load().security.outside_home)
    except Exception:
        logger.warning("outside_home: config unreadable; no place outside the home is read")
        return set()


def allowed(place_id: str) -> bool:
    return place_id in allowed_ids()


def place_path(place_id: str) -> Path | None:
    """The folder of a single-folder place when the owner allowed it, else ``None``."""
    if not allowed(place_id):
        return None
    if place_id == AGENT_SKILLS:
        return _agent_skills_dir()
    if place_id == HUGGINGFACE_CACHE:
        return huggingface_home()
    return None


def not_allowed_reason(place: str) -> str:
    """The sentence for a place PersonalClaw did not read because it is not allowed."""
    return (
        f"PersonalClaw has not been allowed to read {place}. Turn it on in Settings → Security "
        "→ Outside PersonalClaw's home."
    )


# ── Settling what earlier releases left outside the home ─────────────────────────────────────
#
# Earlier releases installed skills into ~/.agents/skills and defaulted the workspace to
# ~/workplace/personalclaw-workspace. Both are settled ONCE, at gateway start, and never touch a
# file outside the home: the skills PersonalClaw installed are copied home, and a saved workspace
# pointer that is just the old default is let go of so the new default applies.

_SETTLED_FILENAME = ".outside-home-settled.json"
_LOCK_FILENAME = ".pclaw-lock.json"


def _old_default_workspace() -> Path:
    return Path.home() / "workplace" / "personalclaw-workspace"


def _links(directory: str, names: list[str]) -> list[str]:
    return [n for n in names if os.path.islink(os.path.join(directory, n))]


def settle_previous_locations() -> dict[str, object]:
    """Copy home the skills PersonalClaw installed in ``~/.agents/skills``, and drop a saved
    workspace pointer that is only the old default. Idempotent, and each step runs once per home:
    the record in the home says it is done, so a later choice of the same folder is kept.

    Nothing outside the home is written, moved or deleted. Returns what it did."""
    from personalclaw.config.loader import config_dir
    from personalclaw.skills.loader import skills_dir

    home = config_dir()
    record_path = home / _SETTLED_FILENAME
    try:
        done = json.loads(record_path.read_text(encoding="utf-8"))
        if not isinstance(done, dict):
            done = {}
    except (OSError, ValueError):
        done = {}
    report: dict[str, object] = {}

    if not done.get("skills"):
        copied: list[str] = []
        shared = _agent_skills_dir()
        target_root = skills_dir()
        try:
            candidates = sorted(shared.iterdir()) if shared.is_dir() else []
        except OSError:
            candidates = []
        for src in candidates:
            if not (src.is_dir() and (src / _LOCK_FILENAME).is_file()):
                continue  # not PersonalClaw's: the owner's own skill stays theirs
            dest = target_root / src.name
            if dest.exists():
                continue
            try:
                # Real files only: a link in that folder would make the home copy read outside it.
                shutil.copytree(src, dest, ignore=_links)
                copied.append(src.name)
            except OSError:
                logger.warning("outside_home: could not copy skill %s home", src, exc_info=True)
        report["skills_copied"] = copied
        done["skills"] = True

    if not done.get("workspace"):
        pointer = home / "workspace_dir"
        try:
            saved = pointer.read_text(encoding="utf-8").strip() if pointer.is_file() else ""
        except OSError:
            saved = ""
        old = _old_default_workspace()
        if saved and Path(saved).expanduser() == old:
            pointer.unlink(missing_ok=True)
            report["workspace_pointer_dropped"] = str(old)
        done["workspace"] = True

    try:
        record_path.write_text(json.dumps(done, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.warning("outside_home: could not record the settled locations", exc_info=True)
    return report
