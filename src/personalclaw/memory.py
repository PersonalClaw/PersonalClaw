"""Persistent memory — structured files, daily history, and FTS5 search.

Structure:
    ~/.personalclaw/workspace/memory/
    ├── preferences.md      # Learned user preferences
    ├── projects.md         # Active project context
    └── history/
        └── 2026-02-16.md   # Daily conversation summaries

    ~/.personalclaw/memory_index.db  # FTS5 full-text search index
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

if TYPE_CHECKING:
    from personalclaw.vector_memory import VectorMemoryStore

logger = logging.getLogger(__name__)

# ── Paths ──

WORKSPACE_DIR_NAME = "workspace"
MEMORY_DIR_NAME = "memory"
HISTORY_DIR_NAME = "history"
PREFERENCES_FILE = "preferences.md"
PROJECTS_FILE = "projects.md"

_DEFAULT_PREFERENCES = "# User Preferences\n\n<!-- Learned from conversations -->\n"
_DEFAULT_PROJECTS = "# Active Projects\n\n<!-- Current work context -->\n"


def workspace_dir() -> Path:
    return config_dir() / WORKSPACE_DIR_NAME


def memory_dir() -> Path:
    return workspace_dir() / MEMORY_DIR_NAME


# ── MemoryStore ──


class MemoryStore:
    """The markdown projection layer: preferences.md, projects.md, daily history,
    FTS5 search over them.

    Post-M2 this is NOT a ``MemoryProvider`` — the provider seam is the record/
    vector store (``VectorMemoryStore``). This class is the human-readable
    *projection* the ``MemoryService`` composes for prompt context + the
    Obsidian-style FS mirror. Its files are a view, not a parallel store.
    """

    @property
    def name(self) -> str:
        return "native"

    def __init__(self, workspace: Path | None = None):
        self._workspace = workspace or workspace_dir()
        self._memory_dir = self._workspace / MEMORY_DIR_NAME
        self._history_dir = self._memory_dir / HISTORY_DIR_NAME
        self._preferences_file = self._memory_dir / PREFERENCES_FILE
        self._projects_file = self._memory_dir / PROJECTS_FILE
        self._index_db = (workspace or config_dir()) / "memory_index.db"
        self._vector_store: "VectorMemoryStore | None" = None

    @property
    def vector_store(self) -> "VectorMemoryStore | None":
        return self._vector_store

    @vector_store.setter
    def vector_store(self, store: "VectorMemoryStore | None") -> None:
        self._vector_store = store

    def init(self) -> None:
        """Create directory structure and default files."""
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._history_dir.mkdir(parents=True, exist_ok=True)
        if not self._preferences_file.exists():
            atomic_write(self._preferences_file, _DEFAULT_PREFERENCES)
        if not self._projects_file.exists():
            atomic_write(self._projects_file, _DEFAULT_PROJECTS)

    # ── Preferences ──

    def read_preferences(self) -> str:
        """Read user preferences markdown file."""
        if self._preferences_file.exists():
            return self._preferences_file.read_text(encoding="utf-8")
        return ""

    def write_preferences(self, content: str) -> None:
        """Write user preferences and update FTS index."""
        atomic_write(self._preferences_file, content)
        self._index_file(self._preferences_file, content)

    def add_preference(self, preference: str) -> None:
        """Append a preference line, avoiding duplicates."""
        content = self.read_preferences()
        if preference not in content:
            content += f"- {preference}\n"
            self.write_preferences(content)

    # ── Projects ──

    def read_projects(self) -> str:
        """Read active projects markdown file."""
        if self._projects_file.exists():
            return self._projects_file.read_text(encoding="utf-8")
        return ""

    def write_projects(self, content: str) -> None:
        """Write active projects, adding header if missing, and update FTS index."""
        date = datetime.now().strftime("%Y-%m-%d")
        # Don't double-wrap if content already has the header
        if content.strip().startswith("# Active Projects"):
            full = content.strip() + "\n"
        else:
            full = f"# Active Projects\n\n_Updated: {date}_\n\n{content}\n"
        atomic_write(self._projects_file, full)
        self._index_file(self._projects_file, full)

    # ── Combined read/write (used by consolidator) ──

    def read(self) -> str:
        """Read preferences + projects as combined memory."""
        parts: list[str] = []
        prefs = self.read_preferences()
        if prefs.strip() and prefs.strip() != _DEFAULT_PREFERENCES.strip():
            parts.append(prefs)
        projects = self.read_projects()
        if projects.strip() and projects.strip() != _DEFAULT_PROJECTS.strip():
            parts.append(projects)
        return "\n\n".join(parts)

    def write(self, content: str) -> None:
        """Write combined memory — splits into preferences + projects sections."""
        if "# Active Projects" in content:
            idx = content.index("# Active Projects")
            self.write_preferences(content[:idx].strip() + "\n")
            # Write directly + index (not write_projects, which adds a header)
            projects_content = content[idx:].strip() + "\n"
            atomic_write(self._projects_file, projects_content)
            self._index_file(self._projects_file, projects_content)
        else:
            self.write_preferences(content)

    # ── Daily History ──

    def _today_history_file(self) -> Path:
        date = datetime.now().strftime("%Y-%m-%d")
        return self._history_dir / f"{date}.md"

    def append_history(self, entry: str) -> None:
        """Append a timestamped entry to today's daily history file."""
        self._history_dir.mkdir(parents=True, exist_ok=True)
        path = self._today_history_file()
        timestamp = datetime.now().astimezone().strftime("%H:%M %Z")

        content = ""
        if path.exists():
            content = path.read_text(encoding="utf-8")
        if not content:
            date = datetime.now().strftime("%Y-%m-%d")
            content = f"# {date}\n"

        content += f"\n#### {timestamp}\n{entry.strip()}\n"
        atomic_write(path, content)
        self._index_file(path, content)

    def prune_history(self, keep_days: int = 365) -> int:
        """Delete daily history files older than *keep_days*. Returns count deleted."""
        if not self._history_dir.exists():
            return 0
        cutoff = datetime.now().date() - timedelta(days=keep_days)
        deleted = 0
        for f in self._history_dir.glob("*.md"):
            try:
                file_date = datetime.strptime(f.stem, "%Y-%m-%d").date()
                if file_date < cutoff:
                    f.unlink()
                    deleted += 1
            except ValueError:
                continue
        if deleted:
            logger.info("Pruned %d history files older than %d days", deleted, keep_days)
        return deleted

    def read_recent_history(self, days: int = 14) -> str:
        """Load daily history with natural decay: recent=full, older=summary."""
        if days <= 0:
            return ""
        parts: list[str] = []
        today = datetime.now().date()
        for i in range(181):
            date = today - timedelta(days=i)
            path = self._history_dir / f"{date.strftime('%Y-%m-%d')}.md"
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                continue

            if i < days:
                parts.append(content)
            elif i < 61:
                parts.append(self._summarize_day(content))
            else:
                n = content.count("####")
                parts.append(f"# {date.strftime('%Y-%m-%d')}\n_{n} conversation(s)_")
        return "\n\n".join(parts)

    @staticmethod
    def _summarize_day(content: str) -> str:
        """Extract header + first entry from a daily history file."""
        sections = content.split("####")
        header = sections[0].strip()
        first = sections[1].strip() if len(sections) > 1 else ""
        result = header + ("\n#### " + first if first else "")
        n_more = len(sections) - 2
        if n_more > 0:
            result += f"\n_…{n_more} more entries_"
        return result

    def read_history(self, days: int = 14) -> str:
        return self.read_recent_history(days=days)

    # ── Context Injection ──

    def render_markdown_context(
        self,
        prefs_cap: int = 4_000,
        projects_cap: int = 6_000,
        history_cap: int = 25_000,
    ) -> list[str]:
        """The markdown projection's context blocks (prefs / projects / history)
        with source citations — the half of memory context THIS layer owns.

        Returns a list of block strings (empty list when nothing to show). The
        Memory Service composes these with the vector layer's L1/semantic/
        episodic blocks and wraps the whole thing — composition is L3's job, not
        this projection's (post-M2 this class no longer reaches the vector store).
        """

        def _cap(text: str, limit: int) -> str:
            if len(text) > limit:
                return text[:limit] + "\n…[truncated]"
            return text

        parts: list[str] = []
        prefs = self.read_preferences()
        if prefs.strip() and prefs.strip() != _DEFAULT_PREFERENCES.strip():
            parts.append(
                f"## User Preferences\n"
                f"_[source: {self._preferences_file}]_\n"
                f"{_cap(prefs, prefs_cap)}"
            )
        projects = self.read_projects()
        if projects.strip() and projects.strip() != _DEFAULT_PROJECTS.strip():
            parts.append(
                f"## Active Projects\n"
                f"_[source: {self._projects_file}]_\n"
                f"{_cap(projects, projects_cap)}"
            )
        history = self.read_recent_history(days=14)
        if history.strip():
            parts.append(
                f"## Recent History\n"
                f"_[source: {self._history_dir}, last 180 days decaying]_\n"
                f"{_cap(history, history_cap)}"
            )
        return parts

    # ── FTS5 Full-Text Search ──

    def _get_db(self) -> sqlite3.Connection:
        """Get or create the FTS5 database connection."""
        try:
            return self._try_create_db()
        except Exception as e:
            # Self-healing: delete corrupted DB and retry
            logger.warning("FTS index init failed (%s), deleting and retrying", e)
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(self._index_db) + suffix)
                p.unlink(missing_ok=True)
            return self._try_create_db()

    def _try_create_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._index_db))
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
            "path, content, tokenize='porter unicode61')"
        )
        return conn

    def _index_file(self, path: Path, content: str) -> None:
        """Index a single file (incremental update)."""
        conn = None
        try:
            conn = self._get_db()
            path_str = str(path)
            conn.execute("DELETE FROM memory_fts WHERE path = ?", (path_str,))
            conn.execute(
                "INSERT INTO memory_fts (path, content) VALUES (?, ?)",
                (path_str, content),
            )
            conn.commit()
        except Exception:
            logger.debug("FTS index update failed", exc_info=True)
        finally:
            if conn is not None:
                conn.close()

    def rebuild_index(self) -> int:
        """Rebuild the full FTS index from all memory files. Returns file count."""
        files: list[tuple[str, str]] = []
        for path in (self._preferences_file, self._projects_file):
            if path.exists():
                files.append((str(path), path.read_text(encoding="utf-8")))
        if self._history_dir.exists():
            for path in self._history_dir.glob("*.md"):
                files.append((str(path), path.read_text(encoding="utf-8")))

        conn = None
        try:
            conn = self._get_db()
            conn.execute("DELETE FROM memory_fts")
            for path_str, content in files:
                conn.execute(
                    "INSERT INTO memory_fts (path, content) VALUES (?, ?)",
                    (path_str, content),
                )
            conn.commit()
        except Exception:
            logger.warning("FTS rebuild failed", exc_info=True)
        finally:
            if conn is not None:
                conn.close()
        return len(files)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Search memory using FTS5. Returns [{path, snippet, rank}]."""
        conn = None
        try:
            conn = self._get_db()
            cursor = conn.execute(
                "SELECT path, snippet(memory_fts, 1, '>>>', '<<<', '...', 32), rank "
                "FROM memory_fts WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            )
            results = [
                {"path": row[0], "snippet": row[1], "rank": row[2]} for row in cursor.fetchall()
            ]
            return results
        except Exception:
            logger.debug("FTS search failed", exc_info=True)
            return []
        finally:
            if conn is not None:
                conn.close()
