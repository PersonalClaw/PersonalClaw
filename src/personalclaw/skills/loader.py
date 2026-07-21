"""Skills loader — markdown skill files for agent capabilities."""

import hashlib
import logging
import os
import re
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)


SKILLS_DIR_NAME = "skills"
_MIN_TRIGGER_OVERLAP = 0.7

# ── Auto skill creation ──

# Namespace for auto-generated skills — keeps them out of the way of
# hand-authored skills.  Final path: ``~/.personalclaw/skills/auto/<name>/SKILL.md``.
AUTO_SKILL_NAMESPACE = "auto"

# Frontmatter field used to mark a skill as auto-generated.  Absence means
# the skill is hand-authored.
AUTO_SKILL_SOURCE_VALUE = "auto"

# Cap synthesized procedure markdown at 10 KB.  Longer outputs indicate
# the aux LLM failed to stay on-task and should be rejected.
AUTO_SKILL_MAX_PROCEDURE_CHARS = 10_240

# Regex for auto-generated skill name segment validation.  Deliberately
# restrictive — we control the generator so we don't need to accept
# arbitrary unicode.  ``_safe_name`` already rejects ``..`` and ``\``;
# this is an additional sanitization layer specific to auto-gen.
_AUTO_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")

# Bundled fallback — inside the backend package
_BUILTIN_SKILLS_DIR = Path(__file__).parent / "bundled"


@dataclass(frozen=True)
class AutoSkillProvenance:
    """Immutable provenance record for an auto-generated skill.

    Serialized into the SKILL.md YAML frontmatter (``source: auto``,
    ``session_key``, ``created_at``, ``refined_at``, ``reuse_count``) so
    operators can always see how a skill was produced and when it was
    last refined.  Absence of ``source: auto`` identifies the skill as
    hand-authored.
    """

    session_key: str
    created_at: str  # ISO 8601 UTC
    refined_at: str = ""  # ISO 8601 UTC; empty until first refinement
    reuse_count: int = 0

    @staticmethod
    def now_iso() -> str:
        """Return the current time as an ISO 8601 UTC string."""
        return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")

    def to_frontmatter_lines(self) -> list[str]:
        """Serialize to the YAML key/value lines used in SKILL.md frontmatter."""
        lines = [
            f"source: {AUTO_SKILL_SOURCE_VALUE}",
            f"session_key: {self.session_key}",
            f"created_at: {self.created_at}",
        ]
        if self.refined_at:
            lines.append(f"refined_at: {self.refined_at}")
        if self.reuse_count:
            lines.append(f"reuse_count: {self.reuse_count}")
        return lines


def _auto_name_from_title(raw: str) -> str:
    """Convert a free-form title into a safe ``auto/<slug>`` skill name.

    Strategy:
    - lowercase
    - replace any run of non-alphanumerics with a single hyphen
    - strip leading/trailing hyphens
    - truncate to 62 chars (leaves room for uniqueness suffix)

    Returns the slug component only; caller prepends the namespace.
    Returns an empty string if the input can't be sanitized.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:62].rstrip("-")
    if not _AUTO_NAME_PATTERN.match(slug):
        return ""
    return slug


def _build_auto_skill_content(
    *,
    slug: str,
    description: str,
    triggers: str,
    procedure_md: str,
    provenance: AutoSkillProvenance,
) -> str:
    """Render a complete ``SKILL.md`` body for an auto-generated skill.

    Layout::

        ---
        name: auto/<slug>
        description: <description>
        triggers: <comma-separated triggers>
        source: auto
        session_key: <session>
        created_at: <iso8601>
        refined_at: <iso8601>      # omitted if empty
        reuse_count: <int>         # omitted if 0
        ---

        # <slug> (auto-generated)

        <procedure_md>

    The leading ``---`` keeps this compatible with existing frontmatter
    parsing in ``SkillsLoader._parse_frontmatter``.  YAML values are
    single-line and newline-stripped to stay within the parser's
    ``key: value`` line format.
    """
    name = f"{AUTO_SKILL_NAMESPACE}/{slug}"
    desc_safe = re.sub(r"\s+", " ", description or "").strip() or name
    triggers_safe = re.sub(r"\s+", " ", triggers or "").strip()
    header_lines = [
        "---",
        f"name: {name}",
        f"description: {desc_safe}",
    ]
    if triggers_safe:
        header_lines.append(f"triggers: {triggers_safe}")
    header_lines.extend(provenance.to_frontmatter_lines())
    header_lines.append("---")
    # Normalize line endings, strip leading/trailing blanks so diffs
    # between revisions stay readable.
    body = procedure_md.replace("\r\n", "\n").strip()
    return "\n".join(header_lines) + "\n\n" + body + "\n"


def _project_skills_dir() -> Path | None:
    """Return project-level skills/ dir from PERSONALCLAW_PROJECT_DIR, or None."""
    val = os.environ.get("PERSONALCLAW_PROJECT_DIR")
    if val:
        p = Path(val) / "skills"
        if p.is_dir():
            return p
    return None


def _iter_skill_files(base: Path) -> list[tuple[str, Path]]:
    """Recursively find all SKILL.md files under *base*.

    Returns ``(relative_name, skill_file_path)`` pairs sorted by name.
    The relative name uses ``/`` as separator (e.g. ``utils/tiny-url``).
    """
    results: list[tuple[str, Path]] = []
    if not base.exists():
        return results
    for skill_file in sorted(base.rglob("SKILL.md")):
        # Name is the parent dir's path relative to base
        rel = skill_file.parent.relative_to(base)
        name = str(rel).replace("\\", "/")
        results.append((name, skill_file))
    return results


def _ensure_builtin_skills(base: Path) -> None:
    """Sync built-in skills: copy new/updated, remove stale.

    Supports nested directories (e.g. ``utils/tiny-url/SKILL.md``).
    Copies the entire skill directory (scripts, assets, etc.), not just SKILL.md.
    Removes skills from *base* that no longer exist in any source.
    """
    # Collect all source skill names
    source_names: set[str] = set()
    for src_root in (_project_skills_dir(), _BUILTIN_SKILLS_DIR):
        if not src_root or not src_root.exists():
            continue
        for name, src_file in _iter_skill_files(src_root):
            source_names.add(name)
            src_dir = src_file.parent
            dest_dir = base / name
            dest_file = dest_dir / "SKILL.md"
            if not dest_file.exists() or src_file.stat().st_mtime > dest_file.stat().st_mtime:
                if dest_dir.exists():
                    shutil.rmtree(dest_dir)
                shutil.copytree(src_dir, dest_dir)
                logger.info("Synced skill: %s", name)

    # Remove known stale builtin skills (replaced by MCP tools)
    stale_builtins = {"learn", "subagent", "cron", "personalclaw-core"}
    if base.exists():
        for name in stale_builtins:
            stale = base / name
            if stale.is_dir():
                shutil.rmtree(stale)
                logger.info("Removed stale builtin skill: %s", name)


def skills_dir() -> Path:
    return config_dir() / SKILLS_DIR_NAME


def _agent_slug(agent: str) -> str:
    """Filesystem-safe slug for an agent's per-agent dir name.

    Canonicalizes default-agent spellings to one key (matching agent-scoped
    memory), then sanitizes to ``[a-z0-9._-]`` so the dir name can't traverse or
    collide with control chars. Mirrors the agent-scoped-hooks convention."""
    from personalclaw.agents.defaults import normalize_agent_name

    name = normalize_agent_name(agent) or "personalclaw"
    # Collapse any dotted run to a single '-' first (kills '..' traversal + leading
    # dots), then map remaining unsafe chars to '-'.
    slug = re.sub(r"\.+", "-", name)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", slug).strip("-_").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or "personalclaw"


def agent_skills_dir(agent: str) -> Path:
    """The agent-local skills tier: ``~/.personalclaw/agents/<slug>/skills/``.

    Highest-precedence tier (skill-agent-local-tier) — a skill here overrides a
    same-named bundled/global one, but only for this agent. Sits under the same
    per-agent dir root as agent-scoped hooks/config."""
    return config_dir() / "agents" / _agent_slug(agent) / SKILLS_DIR_NAME


class SkillsLoader:
    """Load skill markdown files from ~/.personalclaw/skills/.

    Supports nested directories. Each skill is identified by its
    relative path from the skills root (e.g. ``utils/tiny-url``).

    Directory layout::

        ~/.personalclaw/skills/
        ├── learn/SKILL.md
        ├── subagent/SKILL.md
        ├── code/
        │   ├── workspace-tools/SKILL.md
        │   └── code-task-generation/SKILL.md
        └── utils/
            ├── tiny-url/SKILL.md
            └── mcp-debug/SKILL.md
    """

    def __init__(
        self,
        skills_path: Path | None = None,
        install_builtins: bool = True,
        *,
        agent: str | None = None,
    ):
        # An explicit skills_path scopes the loader to that one library — it does
        # NOT also fan out across the global discovery paths. Only the default
        # loader (skills_path=None) aggregates every discovery root.
        self._scoped = skills_path is not None
        self._dir = skills_path or skills_dir()
        # Agent-local tier (skill-agent-local-tier): when an agent context is
        # given, that agent's own skills dir takes precedence over bundled/global
        # for that agent only — an agent can override or add skills nobody else
        # sees. Only meaningful on the default (non-scoped) loader; a scoped
        # loader stays confined to its one library. None = no agent tier.
        self._agent_dir: Path | None = None
        if agent is not None and not self._scoped:
            self._agent_dir = agent_skills_dir(agent)
        if install_builtins:
            _ensure_builtin_skills(self._dir)
        # Cache: path → (mtime, parsed_frontmatter)
        self._fm_cache: dict[str, tuple[float, dict[str, str]]] = {}

    def _iter(self) -> list[tuple[str, Path]]:
        """Return all ``(name, skill_file)`` pairs from this loader's directories.

        The default loader aggregates the global discovery paths; a loader
        constructed with an explicit ``skills_path`` stays confined to it.
        """
        # Agent-local tier first, so its slugs win the first-match-wins dedup
        # below (an agent-local skill overrides a same-named global/bundled one).
        results: list[tuple[str, Path]] = []
        if self._agent_dir is not None and self._agent_dir.is_dir():
            results.extend(_iter_skill_files(self._agent_dir))
        results.extend(_iter_skill_files(self._dir))
        if self._scoped:
            return results
        from personalclaw.skills.marketplace import SKILL_DISCOVERY_PATHS

        seen = {name for name, _ in results}
        for extra_dir in SKILL_DISCOVERY_PATHS:
            if extra_dir.is_dir() and extra_dir != self._dir:
                for name, path in _iter_skill_files(extra_dir):
                    if name not in seen:
                        results.append((name, path))
                        seen.add(name)
        return results

    def _cached_frontmatter(self, path: Path) -> dict[str, str]:
        """Parse frontmatter with mtime-based caching."""
        key = str(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        cached = self._fm_cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
        meta = self._parse_frontmatter(path)
        self._fm_cache[key] = (mtime, meta)
        return meta

    def list_skills(self, *, with_usage: bool = False) -> list[dict]:
        """Return list of skill metadata dicts with key, name, description, path, dir, always.

        When *with_usage* is set, each dict also carries ``use_count`` and
        ``last_used_at`` from the sidecar usage counter (skill-use-counter) —
        the live use signal consumed by surfacing-ranking (#26) and the
        library curator (#27). Lazy-imported to avoid an import cycle.
        """
        usage: dict = {}
        if with_usage:
            try:
                from personalclaw.skills.usage import SkillUsageStore

                usage = SkillUsageStore().all_usage()
            except Exception:
                usage = {}
        agent_root = str(self._agent_dir) if self._agent_dir is not None else None
        skills: list[dict] = []
        for name, skill_file in self._iter():
            meta = self._cached_frontmatter(skill_file)
            # An agent-local skill lives under this loader's agent dir — tag it so
            # the UI can badge the tier (skill-agent-local-tier).
            is_agent_local = agent_root is not None and str(skill_file).startswith(agent_root)
            row: dict = {
                "key": name,
                "name": meta.get("name", name),
                "description": meta.get("description", name),
                "triggers": meta.get("triggers", ""),
                "path": str(skill_file),
                "dir": str(skill_file.parent),
                "always": meta.get("always", "").lower() == "true",
                "status": (meta.get("status", "") or "active").lower(),
                "agent_local": is_agent_local,
            }
            if with_usage:
                u = usage.get(name)
                row["use_count"] = u.count if u else 0
                row["last_used_at"] = u.last_used_at if u else ""
            skills.append(row)
        return skills

    @staticmethod
    def _safe_name(name: str) -> bool:
        """Return True if skill name is safe (no path traversal)."""
        return bool(name) and ".." not in name and "\\" not in name

    def _search_dirs(self) -> list[Path]:
        """Directories this loader resolves names against.

        A scoped loader (explicit ``skills_path``) confines all lookups to its
        own dir; the default loader fans out across the global discovery paths.
        """
        if self._scoped:
            return [self._dir]
        from personalclaw.skills.marketplace import SKILL_DISCOVERY_PATHS

        # Agent-local dir leads so an agent's own skill overrides a global one.
        agent_dirs = [self._agent_dir] if self._agent_dir is not None else []
        return agent_dirs + [self._dir] + SKILL_DISCOVERY_PATHS

    def load_skill(self, name: str) -> str | None:
        """Load a single skill's content by name, searching this loader's dirs."""
        if not self._safe_name(name):
            return None
        for search_dir in self._search_dirs():
            skill_file = search_dir / name / "SKILL.md"
            if skill_file.exists():
                return skill_file.read_text(encoding="utf-8")
        return None

    @property
    def _write_dir(self) -> Path:
        """Where new skills are written. An agent-scoped loader writes into its
        agent-local tier (so create/read agree — writing global while reading
        agent-local would be a split brain); otherwise the loader's base dir."""
        return self._agent_dir if self._agent_dir is not None else self._dir

    def create_skill(self, name: str, content: str) -> bool:
        """Create a new skill directory with SKILL.md.  Returns True on success.

        Writes into the loader's write tier (agent-local when agent-scoped, else
        the base skills dir) — matching where the same loader would resolve it."""
        if not self._safe_name(name):
            return False
        skill_dir = self._write_dir / name
        if skill_dir.exists():
            return False
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        logger.info("Created skill: %s", name)
        return True

    def update_skill(self, name: str, content: str) -> bool:
        """Overwrite an existing skill's SKILL.md.  Returns True if found."""
        if not self._safe_name(name):
            return False
        skill_file = self._dir / name / "SKILL.md"
        if not skill_file.exists():
            return False
        skill_file.write_text(content, encoding="utf-8")
        logger.info("Updated skill: %s", name)
        return True

    def delete_skill(self, name: str) -> bool:
        """Delete a skill directory from any discovery path.  Returns True if found and removed."""
        if not self._safe_name(name):
            return False
        for search_dir in self._search_dirs():
            skill_dir = search_dir / name
            if skill_dir.is_dir():
                shutil.rmtree(skill_dir)
                logger.info("Deleted skill: %s (from %s)", name, search_dir)
                return True
        return False

    # ── Auto skill creation ──

    def is_auto_generated(self, name: str) -> bool:
        """Return True if *name* refers to a skill in the auto namespace.

        Cheap filesystem check (no frontmatter parse) based on the
        directory prefix.  Used for filtering and safety guards (e.g.
        refusing to overwrite a hand-authored skill from an auto-update
        path).
        """
        if not self._safe_name(name):
            return False
        return name.startswith(f"{AUTO_SKILL_NAMESPACE}/")

    def find_similar(
        self,
        description: str,
        threshold: float = 0.85,
        *,
        exclude: str = "",
    ) -> str | None:
        """Return the name of an existing skill whose description overlaps with *description*.

        Uses case-insensitive word-set Jaccard-like overlap against every
        loaded skill's ``description`` frontmatter value:

            score = |words(a) ∩ words(b)| / |words(a) ∪ words(b)|

        Intended for deduplication of auto-generated skills — we don't
        want the agent producing a near-duplicate of an existing skill.
        Returns the first skill whose score ≥ *threshold*, or ``None``
        if nothing matches.

        *exclude* lets callers suppress self-matches during refinement.
        """
        if not description:
            return None
        query_words = set(re.findall(r"\w+", description.lower()))
        if not query_words:
            return None
        best_name: str | None = None
        best_score: float = 0.0
        for name, skill_file in self._iter():
            if exclude and name == exclude:
                continue
            meta = self._cached_frontmatter(skill_file)
            existing = meta.get("description", "")
            if not existing:
                continue
            existing_words = set(re.findall(r"\w+", existing.lower()))
            if not existing_words:
                continue
            intersection = query_words & existing_words
            union = query_words | existing_words
            score = len(intersection) / len(union) if union else 0.0
            if score > best_score:
                best_score = score
                best_name = name
        if best_score >= threshold:
            return best_name
        return None

    def create_auto_skill(
        self,
        slug: str,
        *,
        description: str,
        triggers: str,
        procedure_md: str,
        provenance: AutoSkillProvenance,
    ) -> str | None:
        """Write a new auto-generated skill under ``auto/<slug>/SKILL.md``.

        Returns the full skill name (``auto/<slug>``) on success, or
        ``None`` if the slug is invalid or the skill already exists.

        Caller is responsible for:
        - Running ``find_similar()`` first to avoid near-duplicates.
        - Passing already-redacted ``procedure_md`` (sensitive data is
          the caller's responsibility — this method is pure I/O).
        - Enforcing the ``skills.auto_create_from_sessions`` config flag.
        """
        if not _AUTO_NAME_PATTERN.match(slug):
            logger.warning("Rejected auto skill: slug %r failed validation", slug)
            return None
        if len(procedure_md) > AUTO_SKILL_MAX_PROCEDURE_CHARS:
            logger.warning(
                "Rejected auto skill %s: procedure %d chars exceeds cap %d",
                slug,
                len(procedure_md),
                AUTO_SKILL_MAX_PROCEDURE_CHARS,
            )
            return None
        name = f"{AUTO_SKILL_NAMESPACE}/{slug}"
        skill_dir = self._dir / name
        if skill_dir.exists():
            logger.info("Auto skill %s already exists, skipping", name)
            return None
        content = _build_auto_skill_content(
            slug=slug,
            description=description,
            triggers=triggers,
            procedure_md=procedure_md,
            provenance=provenance,
        )
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        logger.info("Created auto skill: %s", name)
        return name

    def update_auto_skill(
        self,
        name: str,
        *,
        description: str,
        triggers: str,
        procedure_md: str,
        provenance: AutoSkillProvenance,
    ) -> bool:
        """Update an existing auto-generated skill with a refined procedure.

        Refuses to overwrite skills NOT in the auto namespace — protects
        hand-authored skills from being clobbered by the refine path.
        Returns True on success.

        Caller is responsible for passing already-redacted ``procedure_md``.
        """
        if not self.is_auto_generated(name):
            logger.warning(
                "Refusing to auto-refine non-auto skill: %s (not in %s/)",
                name,
                AUTO_SKILL_NAMESPACE,
            )
            return False
        skill_file = self._dir / name / "SKILL.md"
        if not skill_file.exists():
            return False
        if len(procedure_md) > AUTO_SKILL_MAX_PROCEDURE_CHARS:
            logger.warning(
                "Refusing to refine %s: procedure %d chars exceeds cap %d",
                name,
                len(procedure_md),
                AUTO_SKILL_MAX_PROCEDURE_CHARS,
            )
            return False
        # Preserve the original creation timestamp — refinement must not
        # clobber provenance history.  Callers typically pass a fresh
        # provenance with created_at=now; we override from the existing
        # frontmatter here so the write path is authoritative.  Uses
        # ``dataclasses.replace`` because AutoSkillProvenance is frozen.
        existing_meta = self._cached_frontmatter(skill_file)
        original_created_at = existing_meta.get("created_at")
        if original_created_at:
            provenance = replace(provenance, created_at=original_created_at)
        slug = name.split("/", 1)[1]
        content = _build_auto_skill_content(
            slug=slug,
            description=description,
            triggers=triggers,
            procedure_md=procedure_md,
            provenance=provenance,
        )
        skill_file.write_text(content, encoding="utf-8")
        logger.info("Refined auto skill: %s", name)
        return True

    def list_auto_skills(self) -> list[dict]:
        """Return metadata dicts for all skills under the auto namespace.

        Dashboard / CLI consumers use this to display provenance to
        users.  Hand-authored skills are excluded.
        """
        return [s for s in self.list_skills() if s["key"].startswith(f"{AUTO_SKILL_NAMESPACE}/")]

    def get_always_skills(self) -> list[str]:
        """Return names of skills marked ``always: true`` in frontmatter."""
        result: list[str] = []
        for name, skill_file in self._iter():
            meta = self._cached_frontmatter(skill_file)
            if meta.get("always", "").lower() == "true":
                result.append(name)
        return result

    def get_triggered_skills(self, text: str) -> list[str]:
        """Return names of skills whose triggers match the given text.

        Uses word-overlap matching with multi-word trigger phrases and
        negative keywords.  Triggers are comma-separated phrases in the
        ``triggers`` frontmatter field.  A phrase prefixed with ``!`` is a
        negative trigger — if *any* negative trigger matches, the skill is
        excluded regardless of positive matches.

        Returns up to ``max_triggered`` skills sorted by best overlap score.
        """
        from personalclaw.config.loader import AppConfig
        from personalclaw.sel import sel

        cfg = AppConfig.load()
        text_words = set(re.findall(r"\w+", text.lower()))
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

        scored: list[tuple[str, float]] = []
        for name, skill_file in self._iter():
            meta = self._cached_frontmatter(skill_file)
            if meta.get("always", "").lower() == "true":
                continue
            triggers = meta.get("triggers", "")
            if not triggers:
                continue

            # Split into positive and negative triggers
            negated = False
            best_overlap = 0.0
            for trigger in triggers.split(","):
                trigger = trigger.strip().lower()
                if not trigger:
                    continue
                # Negative trigger: "!search" excludes if "search" words match
                if trigger.startswith("!"):
                    neg_words = set(re.findall(r"\w+", trigger[1:]))
                    if neg_words and neg_words <= text_words:
                        negated = True
                        break
                else:
                    trigger_words = set(re.findall(r"\w+", trigger))
                    if not trigger_words:
                        continue
                    overlap = len(trigger_words & text_words) / len(trigger_words)
                    best_overlap = max(best_overlap, overlap)

            if negated:
                sel().log_tool_invocation(
                    session_key="skills",
                    tool_name="skill_trigger",
                    tool_kind="permission",
                    outcome="not_triggered",
                    metadata={"skill": name, "reason": "negative_trigger", "text_hash": text_hash},
                )
                continue

            outcome = "triggered" if best_overlap >= _MIN_TRIGGER_OVERLAP else "not_triggered"
            sel().log_tool_invocation(
                session_key="skills",
                tool_name="skill_trigger",
                tool_kind="permission",
                outcome=outcome,
                metadata={
                    "skill": name,
                    "overlap": round(best_overlap, 2),
                    "text_hash": text_hash,
                },
            )
            if best_overlap >= _MIN_TRIGGER_OVERLAP:
                scored.append((name, best_overlap))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in scored[: cfg.skills.max_triggered]]

    def get_surfaced_skills(self, text: str) -> list[str]:
        """Return skills for this turn via semantic ∪ keyword surfacing (#26).

        Embedding-preferred (reusing the active memory embedder), with the
        keyword trigger path as a cheap union member / no-embedder fallback;
        ranked by relevance then proven use_count (#25). Falls back to the pure
        keyword path :meth:`get_triggered_skills` on any error so a surfacing
        failure can never break a turn.
        """
        from personalclaw.config.loader import AppConfig

        try:
            from personalclaw.skills.surfacing import surface_skills

            cfg = AppConfig.load()
            skills = self.list_skills(with_usage=True)
            surfaced = surface_skills(text, skills, max_skills=cfg.skills.max_triggered)
            if surfaced:
                # Audit the injection decision (replaces per-candidate skill_trigger
                # telemetry from the keyword-only path with the actual outcome).
                from personalclaw.sel import sel

                sel().log_tool_invocation(
                    session_key="skills",
                    tool_name="skill_surface",
                    tool_kind="permission",
                    outcome="surfaced",
                    metadata={
                        "skills": surfaced,
                        "text_hash": hashlib.sha256(text.encode()).hexdigest()[:16],
                    },
                )
            return surfaced
        except Exception:
            logger.debug("Semantic skill surfacing failed; using keyword path", exc_info=True)
            return self.get_triggered_skills(text)

    def get_context(self, *, agent: str | None = None) -> str:
        """Build skills context for prompt injection.

        Always-loaded skills: full content included.
        Other skills: summary with instruction to load via bash when needed.

        When ``agent`` is given AND that agent has an agent-local skills dir
        (skill-agent-local-tier), resolve through an agent-scoped view so the
        agent's own skills override same-named global/bundled ones for its turn.
        The agent-scoped loader is only built when the dir actually exists, so the
        common (no agent-local skills) path pays nothing."""
        if agent is not None and not self._scoped and not self._agent_dir:
            adir = agent_skills_dir(agent)
            if adir.is_dir() and any(adir.iterdir()):
                # Transient agent-scoped view; shares nothing mutable with self.
                scoped = SkillsLoader(install_builtins=False, agent=agent)
                return scoped.get_context()
        always = self.get_always_skills()
        all_skills = self.list_skills()
        if not all_skills:
            return ""

        parts: list[str] = []

        # Full content for always-loaded skills
        for name in always:
            content = self.load_skill(name)
            if content:
                stripped = self.strip_frontmatter(content)
                parts.append(f"### Skill: {name}\n\n{stripped}")

        # Index (progressive disclosure, #29) for on-demand skills. Archived skills
        # (curator #27) are kept off the index. Phase 2 = the agent pulls a full
        # body via skill_invoke{name}, which also records the use (#25).
        on_demand = [
            s for s in all_skills if s["name"] not in always and s.get("status") != "archived"
        ]
        if on_demand:
            summary_lines = [
                "## Available Skills",
                "",
                "If a user request relates to any skill below, load its full steps "
                "first with `skill_invoke{name}` before responding (this also records "
                "the skill as used). To run a skill's scripts, `cd` into its dir first.",
                "",
            ]
            for s in on_demand:
                summary_lines.append(f"- **{s['name']}**: {s['description']} (dir: `{s['dir']}`)")
            parts.append("\n".join(summary_lines))

        return "[Skills:]\n" + "\n\n---\n\n".join(parts) + "\n[End of skills]\n\n"

    # ── Private ──

    @staticmethod
    def _parse_frontmatter(path: Path) -> dict[str, str]:
        """Parse YAML frontmatter from a markdown file (simple key: value)."""
        content = path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return {}
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return {}
        meta: dict[str, str] = {}
        for line in match.group(1).split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip().strip("\"'")
        return meta

    @staticmethod
    def strip_frontmatter(content: str) -> str:
        """Remove YAML frontmatter from markdown."""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end() :].strip()
        return content
