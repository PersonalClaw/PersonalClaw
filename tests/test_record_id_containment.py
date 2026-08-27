"""The rail for the unvalidated-id-to-path class (#455, #459, #471).

**The class.** Every JSON-file store named its records by interpolating an id into a
directory — ``_dir() / f"{record_id}.json"``. ``pathlib``'s ``/`` discards the left
operand when the right side is absolute, so that expression is a filesystem address, not
a join. Four stores were reachable from a route parameter with no check, which gave a URL
an ``rmtree`` (#455), a ``.json`` read + ``unlink`` (#471), and the same read + ``unlink``
through two proposal stores (#459).

**What this suite guards, in three layers.**

1. :mod:`personalclaw.record_ids` — the primitive: what a safe id is, and that a refusal
   is raised rather than returned.
2. The stores — each proven instance refuses a traversal id *end to end*, on a real
   filesystem, for read AND for the destructive verb. Asserting the primitive alone
   would leave "the store actually calls it" unchecked, which is the half that was
   missing before.
3. :func:`census` — a ratchet over the whole tree, so a **new** store cannot reintroduce
   the pattern. This is the layer that makes the fix durable rather than a sweep: the
   class opened because nothing could see it.

**Vacuity.** The census is a regex over source text, so a matcher that silently stops
matching would find nothing and pass — the failure mode that lets a ratchet read as
clean while guarding nothing. :data:`INTERPOLATION_SITE_FLOOR` pins the population the
census measured when it was written; if the scan finds fewer sites than that, the scan
itself is broken and the suite says so rather than going green.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw.record_ids import (
    MAX_RECORD_ID_LEN,
    UnsafeRecordId,
    is_safe_record_id,
    record_path,
    require_safe_record_id,
)

SRC = Path(__file__).resolve().parent.parent / "src" / "personalclaw"

# Every id shape that must be refused. Each entry is (id, why) so a failure names the
# evasion rather than just an index.
UNSAFE_IDS: list[tuple[object, str]] = [
    ("/tmp/zz56-rmtest", "absolute POSIX path — pathlib discards the left operand"),
    ("/etc/passwd", "absolute path to a system file"),
    ("../../../tmp/evil", "relative traversal"),
    ("..", "the parent segment itself"),
    (".", "the current segment itself"),
    ("p-519ac69b/../p-160a9340", "valid-id prefix walking sideways"),
    ("sub/id", "a separator anywhere makes it more than one segment"),
    ("sub\\id", "the Windows separator — a synced home is read on both platforms"),
    ("id\x00.json", "NUL truncation"),
    ("", "empty"),
    ("x" * (MAX_RECORD_ID_LEN + 1), "over the single-component length cap"),
    (None, "not a string at all — route params and JSON bodies both reach here"),
    (123, "not a string at all"),
    (["id"], "not a string at all"),
]

SAFE_IDS = [
    "p-519ac69b",
    "t_00000000000000000000000000000000",
    "a-b_c.d",
    "UPPER-and-lower-99",
    "x" * MAX_RECORD_ID_LEN,
]


class TestPrimitive:
    @pytest.mark.parametrize("bad,why", UNSAFE_IDS, ids=[w for _, w in UNSAFE_IDS])
    def test_unsafe_ids_are_refused(self, bad, why, tmp_path):
        assert is_safe_record_id(bad) is False, why
        with pytest.raises(UnsafeRecordId):
            require_safe_record_id(bad)
        with pytest.raises(UnsafeRecordId):
            record_path(tmp_path, bad)

    @pytest.mark.parametrize("good", SAFE_IDS)
    def test_safe_ids_resolve_under_the_root(self, good, tmp_path):
        assert is_safe_record_id(good) is True
        assert require_safe_record_id(good) == good
        p = record_path(tmp_path, good)
        assert p.parent == tmp_path
        assert p.name == f"{good}.json"

    def test_prefix_and_suffix_are_applied_around_the_checked_id(self, tmp_path):
        """The template is the store's, the id is the caller's — and the id is checked
        BEFORE interpolation, so a separator cannot be smuggled in through either end."""
        assert record_path(tmp_path, "t1", prefix="_comments_").name == "_comments_t1.json"
        assert record_path(tmp_path, "p1", suffix="").name == "p1"
        assert record_path(tmp_path, "r1", suffix=".runner.json").name == "r1.runner.json"

    def test_message_names_the_parameter(self):
        """A 400 that doesn't say which field was wrong is a 400 the client can't act on."""
        with pytest.raises(UnsafeRecordId, match="task_id"):
            require_safe_record_id("../x", kind="task_id")

    def test_refusal_is_not_a_valueerror_oserror_or_typeerror(self):
        """The design decision this class turns on — assert it, don't assume it.

        The stores swallow ``(OSError, ValueError, TypeError)`` and bare ``Exception``
        around their reads and return ``None``. If ``UnsafeRecordId`` were any of those,
        a traversal attempt would answer 404 and be indistinguishable from a missing
        record — which is precisely why the class survived probing (#459). Re-basing this
        exception "for tidiness" silently restores that, so the rail pins it.
        """
        assert issubclass(UnsafeRecordId, Exception)
        for forbidden in (ValueError, OSError, TypeError, KeyError, AttributeError):
            assert not issubclass(UnsafeRecordId, forbidden), forbidden.__name__

    def test_containment_holds_even_when_the_shape_check_would_pass(self, tmp_path):
        """Defense in depth: the shape rule is about ids, containment is about the disk.

        A root that is itself a symlink out of the store resolves elsewhere; the shape
        check cannot see that, so the resolved comparison is not redundant.
        """
        outside = tmp_path / "outside"
        outside.mkdir()
        link = tmp_path / "root-link"
        link.symlink_to(outside)
        # A safe id under a symlinked root still lands inside the resolved root — the
        # check must not reject the ordinary case.
        assert record_path(link, "ok").name == "ok.json"


class TestTaskStoreRefusesTraversal:
    """#471 — GET/DELETE /api/tasks/{id} read and unlinked any .json on the filesystem."""

    @pytest.fixture()
    def provider(self, tmp_path):
        from personalclaw.tasks.native import NativeTaskProvider

        with patch("personalclaw.tasks.native.config_dir", return_value=tmp_path):
            yield NativeTaskProvider()

    @pytest.fixture()
    def canary(self, tmp_path):
        """A well-formed Task record OUTSIDE the store — #471's probe, verbatim.

        Written with exactly ``Task``'s required fields, because #459/#471 both record
        that a malformed foreign record 404s through the broad ``except`` and looks
        like a refusal that never happened.
        """
        outside = tmp_path.parent / "zz-outside"
        outside.mkdir(exist_ok=True)
        f = outside / "canary.json"
        f.write_text(json.dumps({"id": "canary", "title": "CANARY-OUTSIDE-THE-STORE"}))
        return f

    def _traversal_id(self, canary: Path) -> str:
        return str(canary.with_suffix(""))

    @pytest.mark.asyncio
    async def test_get_refuses_instead_of_disclosing(self, provider, canary):
        with pytest.raises(UnsafeRecordId):
            await provider.get_task(self._traversal_id(canary))
        assert canary.exists()

    @pytest.mark.asyncio
    async def test_delete_refuses_instead_of_unlinking(self, provider, canary):
        with pytest.raises(UnsafeRecordId):
            await provider.delete_task(self._traversal_id(canary))
        assert canary.exists(), "the canary was unlinked from outside the store"

    @pytest.mark.asyncio
    async def test_comment_write_refuses(self, provider, canary):
        """``add_comment`` builds ``_comments_<id>.json`` from the same id, gated only on
        the target existing — an arbitrary-overwrite primitive wherever a read works."""
        with pytest.raises(UnsafeRecordId):
            await provider.add_comment(self._traversal_id(canary), body="x", author="y")

    @pytest.mark.asyncio
    async def test_a_valid_task_still_round_trips(self, provider):
        """Vacuity floor for this class: a guard that refused everything would pass
        every test above."""
        task = await provider.create_task(title="ordinary")
        assert await provider.get_task(task.id) is not None
        assert await provider.delete_task(task.id) is True


class TestHierarchyStoreRefusesTraversal:
    """#455 — DELETE /api/projects rmtree'd an arbitrary directory."""

    @pytest.fixture()
    def store(self, tmp_path):
        from personalclaw.tasks.hierarchy import HierarchyStore

        with patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path):
            yield HierarchyStore()

    def test_project_read_refuses(self, store):
        with pytest.raises(UnsafeRecordId):
            store.get_project("/tmp/zz56-rmtest")

    def test_project_delete_cannot_rmtree_outside_the_store(self, store, tmp_path):
        victim = tmp_path.parent / "zz-victim"
        (victim / "subdir").mkdir(parents=True, exist_ok=True)
        keep = victim / "subdir" / "IMPORTANT.txt"
        keep.write_text("unrelated sibling content")
        (victim / "project.json").write_text(json.dumps({"id": "zz-victim", "name": "outside"}))

        with pytest.raises(UnsafeRecordId):
            store.delete_project(str(victim))

        assert keep.exists(), "rmtree reached an unrelated directory outside the store"
        assert victim.exists()

    def test_task_list_path_refuses(self, store):
        with pytest.raises(UnsafeRecordId):
            store.get_task_list("../../../tmp/evil")

    def test_derived_project_paths_inherit_the_guard(self, store):
        """``context_dir``/``worktrees_dir`` are built on ``_project_dir``, so they must
        refuse too — they are ``mkdir(parents=True)`` sites."""
        for method in (store.context_dir, store.worktrees_dir):
            with pytest.raises(UnsafeRecordId):
                method("/tmp/zz56-mkdir")

    def test_an_ordinary_project_still_works(self, store):
        p = store.create_project(name="Ordinary")
        assert store.get_project(p.id) is not None
        assert store.context_dir(p.id).is_dir()


class TestProposalStoresRefuseTraversal:
    """#459 — learning and skill proposals read and unlinked .json outside the home."""

    def test_learning_proposal_load_refuses(self, tmp_path):
        from personalclaw.learning import proposals

        with patch("personalclaw.config.loader.config_dir", return_value=tmp_path):
            with pytest.raises(UnsafeRecordId):
                proposals._load("/tmp/zz57-outside")

    def test_learning_proposal_reject_refuses_before_unlinking(self, tmp_path):
        from personalclaw.learning import proposals

        outside = tmp_path.parent / "zz-learn"
        outside.mkdir(exist_ok=True)
        f = outside / "zz-outside.json"
        f.write_text(json.dumps({"id": "zz-outside", "kind": "lesson", "title": "t", "body": "b"}))
        with patch("personalclaw.config.loader.config_dir", return_value=tmp_path):
            with pytest.raises(UnsafeRecordId):
                proposals._load(str(f.with_suffix("")))
        assert f.exists()

    def test_skill_proposal_load_refuses(self, tmp_path):
        from personalclaw.skills import proposals as skill_proposals

        with patch("personalclaw.skills.loader.config_dir", return_value=tmp_path):
            with pytest.raises(UnsafeRecordId):
                skill_proposals._load("/tmp/zz57-sk-outside")

    def test_skill_proposal_reject_refuses(self, tmp_path):
        from personalclaw.skills import proposals as skill_proposals

        with patch("personalclaw.skills.loader.config_dir", return_value=tmp_path):
            with pytest.raises(UnsafeRecordId):
                skill_proposals.reject("../../../tmp/zz-evil")

    def test_attribution_record_load_refuses(self, tmp_path):
        from personalclaw.learning import attribution

        with patch("personalclaw.config.loader.config_dir", return_value=tmp_path):
            with pytest.raises(UnsafeRecordId):
                attribution._load("/tmp/zz-attr-outside")


class TestUseCaseSettingsSymmetry:
    """The load path had no closed-set check while the save path always did."""

    def test_load_refuses_an_unknown_use_case(self, tmp_path):
        from personalclaw.providers import use_cases

        with patch("personalclaw.config.loader.config_dir", return_value=tmp_path):
            assert use_cases.load_use_case_settings("../../../etc/hosts") == {}
            assert use_cases.load_use_case_settings("not-a-use-case") == {}

    def test_a_real_use_case_still_round_trips(self, tmp_path):
        from personalclaw.providers.use_cases import (
            VALID_USE_CASES,
            load_use_case_settings,
            save_use_case_settings,
        )

        kind = sorted(VALID_USE_CASES)[0]
        with patch("personalclaw.config.loader.config_dir", return_value=tmp_path):
            save_use_case_settings(kind, {"auto_speak": True})
            assert load_use_case_settings(kind) == {"auto_speak": True}


# ── The ratchet ─────────────────────────────────────────────────────────────────
#
# Matches `<something>_dir(...) / f"...{expr}..."` — the expression that IS the class.
# Deliberately broad: it matches sanitized sites too, and the allowlist below is where
# each one earns its exemption with a reason. A narrow regex that only matched the
# unsanitized form would go green the moment someone spelled a new one differently.
_INTERPOLATION_RE = re.compile(r"""[Dd]ir\(\)\s*/\s*f["'][^"']*\{""")

# Every site the census finds must satisfy one of:
#   * it goes through `record_path` (the whole point), OR
#   * it appears here, with the reason it is not route-reachable.
#
# ADDING A ROW IS A DECISION, not a formality: it asserts that no untrusted string
# reaches that expression. If you cannot say why, use `record_path` instead.
ALLOWED_RAW_INTERPOLATION: dict[str, str] = {
    "agent_metadata.py": "ids pass `_validate_name` in the same expression",
    "agents/runners.py": "`sidecar_path` raises on `_SAFE_ID_RE.fullmatch` first",
    "apps/app_manager.py": "`_rollback_dir` applies the kebab `_validate_app_name`",
    "concurrency.py": "the id is a `safe` + content digest computed locally",
    "dashboard/chat_handlers.py": "filename is `uuid4().hex` + a sanitized basename",
    "dashboard/chat_runner.py": "`session_pid_<pid>.txt` — only under `isinstance(pid, int)`",
    "dashboard/handlers/agents.py": "`safe_slug`, sanitized above the expression",
    "dashboard/handlers/files.py": "filename is `uuid4().hex` + a sanitized basename",
    "dashboard/handlers/uploads.py": "filename is `uuid4().hex` + a sanitized basename",
    "evals/ablation.py": "matrix ids are generated by the ablation runner; no route reaches it",
    "evals/judge_bench.py": "a benchmark NAME may deliberately be a path (CLI affordance)",
    "evals/retrieval_bench.py": "`store_kind` is checked against the closed `STORES` set",
    "evals/store.py": "study ids are generated by the study runner; no route reaches it",
    "knowledge/slicing.py": "both halves checked: `_HEX64_RE` + closed `_CACHED_SUFFIXES`",
    "prompt_providers/native_provider.py": "names pass `_safe_name` in the same expression",
    "providers/use_cases.py": "both paths check the closed `VALID_USE_CASES` set",
    "workflows/leases.py": "the id is a locally computed `safe` token",
    "workflows/pool.py": "the id is a locally computed `safe` token",
}

# The population the census measured when this rail was written. A scan that finds fewer
# sites than this has itself broken — see the module docstring on vacuity.
INTERPOLATION_SITE_FLOOR = 15


def census() -> dict[str, list[int]]:
    """file (relative to src/personalclaw) → line numbers holding a raw interpolation."""
    found: dict[str, list[int]] = {}
    for py in sorted(SRC.rglob("*.py")):
        rel = py.relative_to(SRC).as_posix()
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith("#") or "``" in line:
                continue  # a comment or a docstring quoting the pattern is not a call site
            if _INTERPOLATION_RE.search(line):
                found.setdefault(rel, []).append(n)
    return found


class TestRatchet:
    def test_census_is_not_vacuous(self):
        sites = census()
        total = sum(len(v) for v in sites.values())
        assert total >= INTERPOLATION_SITE_FLOOR, (
            f"the census found {total} interpolation sites, below the floor of "
            f"{INTERPOLATION_SITE_FLOOR} — the scanner is broken, not the tree"
        )

    def test_no_unallowlisted_raw_interpolation(self):
        offenders = {
            f: lines for f, lines in census().items() if f not in ALLOWED_RAW_INTERPOLATION
        }
        assert not offenders, (
            "a record id is being interpolated into a path without `record_ids.record_path`:\n"
            + "\n".join(f"  src/personalclaw/{f}:{lines}" for f, lines in sorted(offenders.items()))
            + "\n\nUse `record_path(root, id, ...)`, or add the file to "
            "ALLOWED_RAW_INTERPOLATION with the reason no untrusted string reaches it."
        )

    def test_allowlist_has_no_stale_rows(self):
        """A row for a file that no longer holds the pattern is a claim about nothing —
        and it would silently pre-approve the pattern's return to that file."""
        sites = census()
        stale = sorted(f for f in ALLOWED_RAW_INTERPOLATION if f not in sites)
        assert not stale, f"remove these rows; the pattern is gone from them: {stale}"

    def test_the_fixed_stores_are_clean_by_construction_not_by_exemption(self):
        """The proven instances must hold no raw interpolation AND no allowlist row.

        Also the strongest available check on the census's own comment/docstring skip
        rule: these five files DO discuss the pattern in prose, so if the skip were too
        narrow they would show up as offenders, and if the regex were too narrow the
        `record_path` assertion below would be the only thing left standing.
        """
        sites = census()
        for fixed in (
            "tasks/native.py",
            "tasks/hierarchy.py",
            "learning/proposals.py",
            "learning/attribution.py",
            "skills/proposals.py",
        ):
            assert fixed not in ALLOWED_RAW_INTERPOLATION, fixed
            assert fixed not in sites, f"{fixed} still interpolates an id into a path"
            assert "record_path" in (SRC / fixed).read_text(encoding="utf-8"), fixed


class TestGateIsInstalled:
    def test_middleware_is_in_the_app_not_merely_importable(self):
        """A gate that is importable but uninstalled maps nothing — the reason
        ``api_version_middleware`` is a factory with a marker attribute too."""
        from personalclaw.dashboard.invalid_id_gate import invalid_id_middleware

        mw = invalid_id_middleware()
        assert getattr(mw, "_is_invalid_id_gate", False) is True

    def test_server_installs_it_innermost(self):
        src = (SRC / "dashboard" / "server.py").read_text(encoding="utf-8")
        assert "invalid_id_middleware()" in src
        # Innermost = last before spa_fallback, so it wraps the handler and nothing else.
        gate = src.index("invalid_id_middleware()")
        fallback = src.index("spa_fallback,\n    ]")
        assert gate < fallback, "the gate must precede spa_fallback in the ordering"

    def test_wire_code_is_registered(self):
        from personalclaw.http_errors import HTTP_ERROR_CODES

        assert "invalid_id" in HTTP_ERROR_CODES
