"""Native filesystem-backed prompt provider.

Stores each prompt template as a YAML file under ``~/.personalclaw/prompts/`` and
each reusable snippet under ``~/.personalclaw/prompt_snippets/``, with the schema:

    name: code-review
    kind: user                # system | user
    title: Code Review
    description: Review code changes for bugs and style
    tags: [code, review]
    variables:
      - name: language
        type: select
        options: [python, typescript, java, go, rust]
        required: true
      - name: focus
        type: text
        default: general
      - name: diff
        type: textarea
        required: true
    content: |
      Review the following {{language}} code changes.
      Focus on: {{focus}}
      {{> review-guidelines}}     # include a reusable snippet

We use YAML rather than markdown frontmatter so structured fields stay
unambiguous and the content block can hold arbitrary text including its
own ``---`` separators.

Records that predate the ``kind``/``title`` fields or use legacy variable types
(``string``/``file_path``) are migrated to the current shape **on read and
rewritten in place** — no dual-format support, the old shape stops existing.
"""

import logging
import re
from pathlib import Path
from typing import Any

from personalclaw.prompt_providers.base import (
    PromptProvider,
    PromptSnippet,
    PromptTemplate,
)

logger = logging.getLogger(__name__)


_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _yaml_loads(text: str) -> dict[str, Any]:
    """Lazy yaml import so this module loads even when PyYAML isn't installed.

    PyYAML is a runtime dep, but defending against absence keeps the
    provider importable for static checks and cold-path module loads.
    """
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError("PyYAML is required for the native prompt provider") from exc
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("prompt YAML must parse to a mapping at the top level")
    return data


def _yaml_dumps(data: dict[str, Any]) -> str:
    import yaml  # type: ignore

    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _config_dir() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir()


def _prompts_dir() -> Path:
    d = _config_dir() / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _snippets_dir() -> Path:
    d = _config_dir() / "prompt_snippets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(name: str) -> str:
    if not _NAME_RE.match(name or ""):
        raise ValueError(
            "name must match ^[a-zA-Z0-9_-]{1,64}$ " "(only letters, digits, dashes, underscores)"
        )
    return name


def _prompt_path(name: str) -> Path:
    return _prompts_dir() / f"{_safe_name(name)}.yaml"


def _snippet_path(name: str) -> Path:
    return _snippets_dir() / f"{_safe_name(name)}.yaml"


def _mtime(path: Path) -> float:
    """File last-modified epoch seconds (0.0 if it can't be read) — the runtime
    ``updated_at`` the UI sorts by. Not persisted."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _prompt_payload(prompt: PromptTemplate) -> dict[str, Any]:
    """On-disk YAML payload for a prompt — drops empty/derived fields."""
    payload = prompt.to_dict()
    payload.pop("package", None)
    payload.pop("updated_at", None)  # runtime-only (file mtime), never persisted
    # source is persisted only when non-default; a plain user prompt omits it.
    if payload.get("source") in (None, "", "user"):
        payload.pop("source", None)
    if not payload.get("description"):
        payload.pop("description", None)
    if not payload.get("tags"):
        payload.pop("tags", None)
    if not payload.get("variables"):
        payload.pop("variables", None)
    return payload


def _snippet_payload(snippet: PromptSnippet) -> dict[str, Any]:
    payload = snippet.to_dict()
    payload.pop("package", None)
    payload.pop("updated_at", None)  # runtime-only (file mtime), never persisted
    if payload.get("source") in (None, "", "user"):
        payload.pop("source", None)
    if not payload.get("description"):
        payload.pop("description", None)
    if not payload.get("tags"):
        payload.pop("tags", None)
    if not payload.get("variables"):
        payload.pop("variables", None)
    return payload


class NativePromptProvider(PromptProvider):
    @property
    def name(self) -> str:
        return "native"

    @property
    def display_name(self) -> str:
        return "Native Filesystem Prompts"

    # ── prompts ────────────────────────────────────────────────────────────

    def list_prompts(self) -> list[PromptTemplate]:
        out: list[PromptTemplate] = []
        for path in sorted(_prompts_dir().glob("*.yaml")):
            tpl = self._load_prompt(path, path.stem)
            if tpl is not None:
                out.append(tpl)
        return out

    def get_prompt(self, name: str) -> PromptTemplate | None:
        try:
            path = _prompt_path(name)
        except ValueError:
            return None
        if not path.exists():
            return None
        return self._load_prompt(path, name)

    def create_prompt(self, prompt: PromptTemplate) -> PromptTemplate:
        path = _prompt_path(prompt.name)
        if path.exists():
            raise ValueError(f"prompt {prompt.name!r} already exists")
        path.write_text(_yaml_dumps(_prompt_payload(prompt)), encoding="utf-8")
        return prompt

    def update_prompt(self, name: str, prompt: PromptTemplate) -> PromptTemplate:
        # Renames are explicit: caller must delete + create. This keeps
        # references in chat history (@name) stable across edits.
        existing_path = _prompt_path(name)
        if not existing_path.exists():
            raise FileNotFoundError(f"prompt {name!r} does not exist")
        if prompt.name != name:
            raise ValueError("renames are not supported in update_prompt; delete and re-create")
        existing_path.write_text(_yaml_dumps(_prompt_payload(prompt)), encoding="utf-8")
        return prompt

    def delete_prompt(self, name: str) -> bool:
        try:
            path = _prompt_path(name)
        except ValueError:
            return False
        if not path.exists():
            return False
        path.unlink()
        return True

    def _load_prompt(self, path: Path, name: str) -> PromptTemplate | None:
        try:
            raw = _yaml_loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Skipping malformed prompt file %s", path)
            return None
        raw.setdefault("name", name)
        try:
            tpl = PromptTemplate.from_dict(raw)
        except Exception:
            logger.exception("Skipping invalid prompt %s", name)
            return None
        tpl.source = "user"
        tpl.updated_at = _mtime(path)
        # Migrate-on-read: if the canonical on-disk shape differs from what's stored
        # (old record lacked kind/title, used legacy var types), rewrite it now so
        # the old shape stops existing — no dual-format support.
        self._migrate_in_place(path, raw, _prompt_payload(tpl), name)
        return tpl

    # ── snippets ───────────────────────────────────────────────────────────

    def list_snippets(self) -> list[PromptSnippet]:
        out: list[PromptSnippet] = []
        for path in sorted(_snippets_dir().glob("*.yaml")):
            snip = self._load_snippet(path, path.stem)
            if snip is not None:
                out.append(snip)
        return out

    def get_snippet(self, name: str) -> PromptSnippet | None:
        try:
            path = _snippet_path(name)
        except ValueError:
            return None
        if not path.exists():
            return None
        return self._load_snippet(path, name)

    def create_snippet(self, snippet: PromptSnippet) -> PromptSnippet:
        path = _snippet_path(snippet.name)
        if path.exists():
            raise ValueError(f"snippet {snippet.name!r} already exists")
        path.write_text(_yaml_dumps(_snippet_payload(snippet)), encoding="utf-8")
        return snippet

    def update_snippet(self, name: str, snippet: PromptSnippet) -> PromptSnippet:
        existing_path = _snippet_path(name)
        if not existing_path.exists():
            raise FileNotFoundError(f"snippet {name!r} does not exist")
        if snippet.name != name:
            raise ValueError("renames are not supported; delete and re-create")
        existing_path.write_text(_yaml_dumps(_snippet_payload(snippet)), encoding="utf-8")
        return snippet

    def delete_snippet(self, name: str) -> bool:
        try:
            path = _snippet_path(name)
        except ValueError:
            return False
        if not path.exists():
            return False
        path.unlink()
        return True

    def _load_snippet(self, path: Path, name: str) -> PromptSnippet | None:
        try:
            raw = _yaml_loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Skipping malformed snippet file %s", path)
            return None
        raw.setdefault("name", name)
        try:
            snip = PromptSnippet.from_dict(raw)
        except Exception:
            logger.exception("Skipping invalid snippet %s", name)
            return None
        snip.source = "user"
        snip.updated_at = _mtime(path)
        self._migrate_in_place(path, raw, _snippet_payload(snip), name)
        return snip

    @staticmethod
    def _migrate_in_place(
        path: Path, raw: dict[str, Any], canonical: dict[str, Any], name: str
    ) -> None:
        """Rewrite a record whose stored shape differs from the canonical one.

        Migrate-on-read is *shape* normalization (kind/title/legacy var types) — it must
        preserve seed-metadata that intentionally lives OUTSIDE the template dataclass, or
        it fights the seeder forever. ``bundled_sha`` is the seeder's pristine-check stamp
        (written by :func:`seed_bundled_system_prompts` to detect user edits); the canonical
        payload derives from ``to_dict()`` and never emits it, so stripping it here would
        make ``canonical != raw`` on EVERY load → a rewrite + re-stamp ping-pong on every
        read. Carry such sidecar keys forward so the comparison converges to a fixed point.
        """
        canonical = dict(canonical)
        for sidecar in ("bundled_sha",):
            if sidecar in raw:
                canonical[sidecar] = raw[sidecar]
        if canonical == raw:
            return
        try:
            path.write_text(_yaml_dumps(canonical), encoding="utf-8")
            logger.info("Migrated prompt record %r to the current schema", name)
        except OSError:
            logger.debug("Could not migrate %s in place", name, exc_info=True)


def seed_bundled_system_prompts() -> None:
    """Seed every catalog-declared bundled prompt as an editable native prompt.

    Writes one prompt per :data:`catalog.BUNDLED_PROMPTS` row from
    ``config/prompts/<file>`` on first run so each shows up in the Prompts UI and
    is bound to its use-case by default. Idempotent and non-clobbering: an
    existing file (possibly user-edited) is left untouched.
    """
    import os

    from personalclaw.prompt_providers.catalog import BUNDLED_PROMPTS

    # Opt-out for tests that assert on a clean, user-only prompt store.
    if os.environ.get("PERSONALCLAW_SKIP_PROMPT_SEED"):
        return
    # Seed the shared snippets FIRST so the prompts' {{> name}} includes resolve.
    seed_bundled_snippets()
    src_dir = Path(__file__).resolve().parent.parent / "config" / "prompts"
    for entry in BUNDLED_PROMPTS:
        try:
            path = _prompt_path(entry.name)
        except ValueError:
            continue
        if path.exists():
            continue
        try:
            content = (src_dir / entry.filename).read_text(encoding="utf-8")
        except OSError:
            continue
        tpl = PromptTemplate(
            name=entry.name,
            kind=entry.kind,  # type: ignore[arg-type]
            description=entry.description,
            content=content,
            variables=list(entry.variables),
            tags=list(entry.tags),
        )
        try:
            path.write_text(_yaml_dumps(_prompt_payload(tpl)), encoding="utf-8")
            logger.info("Seeded bundled prompt %r", entry.name)
        except OSError:
            logger.debug("Failed to seed bundled prompt %r", entry.name, exc_info=True)


def seed_bundled_app_prompts() -> None:
    """Seed the prompts OWNED by Tier-1 native apps (``apps/native/``).

    Native apps are the always-present shipped baseline, so the prompts they ship
    are seeded alongside core's here — early, before extension *provider* discovery
    — rather than only on a per-app enable. This keeps the prompt system
    self-sufficient: a context that uses the prompt provider (the gateway, the CLI,
    a unit test) resolves an app-owned use-case like ``knowledge_extraction`` even
    before discovery has run. Idempotent + non-clobbering (a later lifecycle seed on
    enable is a no-op), gated by ``PERSONALCLAW_SKIP_PROMPT_SEED``."""
    import os

    if os.environ.get("PERSONALCLAW_SKIP_PROMPT_SEED"):
        return
    try:
        from personalclaw.apps.manifest import AppManifest
        from personalclaw.apps.prompt_seed import seed_app_prompts
        from personalclaw.providers.loader import BUNDLED_DIR
    except Exception:
        return
    if not BUNDLED_DIR.is_dir():
        return
    for entry in sorted(BUNDLED_DIR.iterdir()):
        manifest_file = entry / "app.json" if entry.is_dir() else None
        if not manifest_file or not manifest_file.is_file():
            continue
        try:
            manifest = AppManifest.from_json_file(manifest_file)
        except Exception:
            continue
        # Every native app that ships prompts (the dir contains only native apps).
        if not manifest.prompts:
            continue
        try:
            seed_app_prompts(manifest, entry)
        except Exception:
            logger.debug("bundled app %s: prompt seed failed", entry.name, exc_info=True)


def seed_bundled_snippets() -> None:
    """Seed the shared bundled snippets prompts include via {{> name}}.

    New snippets are seeded. An already-seeded snippet is RE-SEEDED when the bundled
    source content has changed AND the on-disk copy is still pristine (the user hasn't
    edited it) — so a bundled update (e.g. a new security guardrail added to
    ``safety-rules.md``) propagates to existing instances, without ever clobbering a
    user's own edit. Pristineness is judged by a ``bundled_sha`` stamp recorded at seed
    time: if the on-disk content still hashes to that stamp, it's untouched. Skipped
    under ``PERSONALCLAW_SKIP_PROMPT_SEED``."""
    import hashlib
    import os

    from personalclaw.prompt_providers.catalog import BUNDLED_SNIPPETS

    if os.environ.get("PERSONALCLAW_SKIP_PROMPT_SEED"):
        return
    src_dir = Path(__file__).resolve().parent.parent / "config" / "prompt_snippets"
    for entry in BUNDLED_SNIPPETS:
        try:
            path = _snippet_path(entry.name)
        except ValueError:
            continue
        try:
            content = (src_dir / entry.filename).read_text(encoding="utf-8").rstrip("\n")
        except OSError:
            continue
        bundled_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

        if path.exists():
            try:
                existing = _yaml_loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            stamp = existing.get("bundled_sha")
            on_disk_content = str(existing.get("content", "")).rstrip("\n")
            on_disk_sha = hashlib.sha256(on_disk_content.encode("utf-8")).hexdigest()
            if on_disk_sha == bundled_sha:
                # Content already matches bundled; ensure the stamp is present, then done.
                if stamp != bundled_sha:
                    existing["bundled_sha"] = bundled_sha
                    try:
                        path.write_text(_yaml_dumps(existing), encoding="utf-8")
                    except OSError:
                        pass
                continue
            # Content differs from bundled. Re-seed ONLY when the on-disk copy is a PROVEN
            # pristine older bundled version: it carries a bundled_sha stamp (written by a
            # PRIOR seed of bundled content) AND its content still hashes to that stamp
            # (untouched since). Then a bundled update propagates.
            if stamp is not None and stamp == on_disk_sha:
                pass  # pristine older bundled copy → fall through to re-seed
            else:
                # No stamp (legacy/pre-migration) OR stamp≠content (user edited): we cannot
                # prove it's bundled, so treat it as USER-OWNED and never re-seed. Mark it
                # so the ambiguous branch isn't re-evaluated every boot (idempotent).
                if not existing.get("user_owned"):
                    existing["user_owned"] = True
                    existing.pop("bundled_sha", None)
                    try:
                        path.write_text(_yaml_dumps(existing), encoding="utf-8")
                    except OSError:
                        pass
                continue

        snip = PromptSnippet(
            name=entry.name,
            description=entry.description,
            content=content,
            variables=list(entry.variables),
            tags=list(entry.tags),
        )
        payload = _snippet_payload(snip)
        payload["bundled_sha"] = bundled_sha  # stamp for the pristine-check on next seed
        try:
            path.write_text(_yaml_dumps(payload), encoding="utf-8")
            logger.info("Seeded/updated bundled snippet %r", entry.name)
        except OSError:
            logger.debug("Failed to seed bundled snippet %r", entry.name, exc_info=True)


def create_provider(config: dict[str, Any] | None = None) -> "NativePromptProvider":
    return NativePromptProvider()
