"""The rows of an "apps cannot" table, run against one installed app the module shares.

Each refusal module used to build a gateway and install its fixture app through preview, consent
and install in every test, once per route. :class:`SharedApp` installs the app once per module
(once per xdist worker), keeps a copy of the home as the install left it, and runs each row of a
table on that copy. Nothing one row did reaches the next:

- **the home** is put back to the copy before every row, only what a row changed written again
  (:class:`laid_tree.Laid`), and its digest is checked against the copy's before the row runs, so
  a row never sees another row's grant, install, setting or file;
- **sessions** are all signed out before every row, and the row gets a gateway of its own, an
  owner token of its own and an app token minted for it;
- **the Security Event Log rows** are read from a recorder made for the row, empty when it starts:
  every row the row's requests write, the token middleware's included (:func:`recording_sel`);
- **the fenced registries** (:func:`state_leak_guard.fenced_state`, what the conftest's fences put
  back once per test) are read before and after every row against the table's first reading. A
  row that changed one fails, and so does the row after it, each by name.

A row is ``(name, body)``: an async function given the :class:`Row` it runs in. ``row()`` reports
a failing row as a subtest named by its name, and the rows after it still run.

A refused row reads the home around the app's requests with :func:`home_files` and
:func:`written_since`, so a refusal that wrote anything fails its own row.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from laid_tree import Laid
from state_leak_guard import fenced_state
from table_rows import row

#: What is reset before a row: the home, put back to what the install left, and the recorder the
#: row's Security Event Log rows go to.
RESETS = frozenset({"home", "sel"})

#: A gateway factory: the routes it serves, and the gateway it yields.
Gateway = Callable[[list[tuple[str, str, Any]]], AbstractAsyncContextManager[Any]]


@contextmanager
def recording_sel(recorder: MagicMock) -> Iterator[None]:
    """Every Security Event Log row written in the block sent to *recorder*, not the home's log.

    Two seams: ``personalclaw.sel.sel``, which every writer but one asks for the log when it
    writes, and the token middleware's own name for it, bound when ``token_auth`` was imported.
    That one writes a request's success row after the handler answers, so a refusal a handler
    answers (rather than raises) would otherwise be seen writing the home's log.
    """
    with (
        patch("personalclaw.sel.sel", return_value=recorder),
        patch("personalclaw.dashboard.token_auth._sel_fn", return_value=recorder),
    ):
        yield


def digest(home: Path) -> str:
    """Every file under *home*, by its path and its bytes."""
    hasher = hashlib.sha256()
    for path in sorted(p for p in home.rglob("*") if p.is_file()):
        hasher.update(path.relative_to(home).as_posix().encode())
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


#: What the token middleware stamps on the session of any authorized request, refused or not:
#: when it was last seen, and from where (``session_store.touch_device_last_seen``).
_SIGN_IN_STAMPS = frozenset({"last_seen", "ip"})


def _unstamped(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _unstamped(v) for k, v in value.items() if k not in _SIGN_IN_STAMPS}
    if isinstance(value, list):
        return [_unstamped(v) for v in value]
    return value


def home_files(home: Path) -> dict[str, bytes]:
    """Every file under *home* by its path, the sessions' sign-in stamps left out of their file."""
    from personalclaw.dashboard.session_store import SESSIONS_FILE

    files = {}
    for path in (p for p in home.rglob("*") if p.is_file()):
        name, raw = path.relative_to(home).as_posix(), path.read_bytes()
        if name == SESSIONS_FILE:
            raw = json.dumps(_unstamped(json.loads(raw)), sort_keys=True).encode()
        files[name] = raw
    return files


def written_since(home: Path, before: dict[str, bytes]) -> list[str]:
    """The files in *home* that differ from *before* (a :func:`home_files` reading), by path."""
    now = home_files(home)
    return sorted(k for k in before.keys() | now.keys() if before.get(k) != now.get(k))


def _changed(was: Path, now: Path) -> list[str]:
    """The files that differ between two homes, by their path in the home."""

    def files(root: Path) -> dict[str, bytes]:
        return {
            p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
        }

    before, after = files(was), files(now)
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))


@dataclass
class Row:
    """What one row runs with: its gateway, the app's token, the home and its SEL recorder."""

    gw: Any
    token: str
    home: Path
    sel: MagicMock
    app: str

    def denials(self, path: str) -> list:
        """The rows the permission middleware wrote refusing this app *path*, in this row."""
        return [
            c
            for c in self.sel.call_args_list
            if c.kwargs.get("caller") == f"app:{self.app}"
            and c.kwargs.get("outcome") == "denied"
            and c.kwargs.get("source") == "app_permissions"
            and c.kwargs.get("resources") == path
        ]


RowBody = Callable[[Row], Awaitable[None]]


@dataclass
class SharedApp:
    """One fixture app, installed once per module and restored before every row."""

    name: str
    #: Writes the app's bundle under the given folder and returns its directory.
    bundle: Callable[[Path], Path]
    gateway: Gateway
    #: What you do once the app is installed, before the home is copied: another app installed
    #: beside it, or a setting of yours saved. Given the gateway the install went through and the
    #: home, so every row starts from it too.
    prepare: Callable[[Any, Path], Awaitable[None]] | None = None
    _home: Path | None = None
    _copy: Path | None = None
    _digest: str = ""
    #: The home as the install left it, read once: what :meth:`restore` puts back.
    _laid: Laid | None = None
    #: What :meth:`run` resets before every row after the first. :func:`planted_rows` takes one
    #: away to show that the check behind it fails the row after the one that left a change.
    resets: frozenset[str] = RESETS

    async def home(self, tmp_path_factory: pytest.TempPathFactory, mp: pytest.MonkeyPatch) -> Path:
        """The module's home, with the app installed: on the first ask, installed through the
        gateway as the owner installs one (preview, then install with its digest) and copied."""
        if self._home is None:
            root = tmp_path_factory.mktemp(f"shared-{self.name}")
            self._home, self._copy = root / "home", root / "installed"
            self._home.mkdir()
            self._point_at(mp)
            async with self.gateway([]) as gw:
                await gw.install(self.bundle(self._home))
                if self.prepare is not None:
                    await self.prepare(gw, self._home)
            shutil.copytree(self._home, self._copy, symlinks=True)
            self._digest = digest(self._copy)
            self._laid = Laid.take(self._copy)
        self._point_at(mp)
        return self._home

    def _point_at(self, mp: pytest.MonkeyPatch) -> None:
        import personalclaw.config.loader as loader
        from personalclaw.apps import manager

        home = self._home
        mp.setattr(loader, "config_dir", lambda: home)
        mp.setattr(manager, "config_dir", lambda: home)

    def restore(self) -> None:
        """The home as the install left it: put back as it was laid out (:class:`laid_tree.Laid`),
        writing only what a row changed, not removed and copied whole for every row."""
        assert self._home is not None and self._laid is not None
        self._laid.reset(self._home)

    async def run(
        self,
        subtests: Any,
        rows: list[tuple[str, RowBody]],
        routes: list[tuple[str, str, Any]],
        *,
        label: str = "case",
    ) -> None:
        """Run each of *rows* on the installed app, behind a gateway serving *routes*."""
        from personalclaw.dashboard.token_auth import revoke_all_sessions

        assert self._home is not None, "ask for the home first"
        assert len(rows) == len({name for name, _ in rows}), "two rows share a name"
        baseline = fenced_state()
        sel = MagicMock()
        for at, (name, body) in enumerate(rows):
            # The first row always starts from the install: a run reaches no row of another run.
            resets = RESETS if at == 0 else self.resets
            with row(subtests, **{label: name}):
                revoke_all_sessions()
                if "home" in resets:
                    self.restore()
                assert digest(self._home) == self._digest, (
                    f"{name} would start in a home other than the installed app's: a row before "
                    f"it left a change behind, in {_changed(self._copy, self._home)}"
                )
                _assert_fences(baseline, f"a row before {name}")
                if "sel" in resets:
                    sel = MagicMock()
                assert (
                    not sel.log_api_access.call_args_list
                ), f"{name} would start with Security Event Log rows a row before it wrote"
                with recording_sel(sel):
                    async with self.gateway(routes) as gw:
                        token = str((await gw.ok("POST", f"/api/apps/{self.name}/token"))["token"])
                        await body(Row(gw, token, self._home, sel.log_api_access, self.name))
                _assert_fences(baseline, name)


def _assert_fences(baseline: dict[str, frozenset[str]], who: str) -> None:
    now = fenced_state()
    changed = {
        k: sorted(baseline[k] ^ now.get(k, frozenset()))
        for k in baseline
        if now.get(k) != baseline[k]
    }
    assert not changed, f"{who} left registries the conftest's fences put back changed: {changed}"


class RecordedRows:
    """A ``subtests`` stand-in that records the rows that failed and why, instead of reporting."""

    def __init__(self) -> None:
        self.failed: dict[str, str] = {}

    @contextmanager
    def test(self, **kwargs: Any) -> Iterator[None]:
        try:
            yield
        except BaseException as exc:  # noqa: BLE001 - every row's failure is what is recorded
            self.failed[" ".join(str(v) for v in kwargs.values())] = str(exc)


async def planted_rows(
    shared: SharedApp,
    routes: list[tuple[str, str, Any]],
    probe: tuple[str, RowBody],
) -> dict[str, dict[str, dict[str, str]]]:
    """What fails when a row leaves the home, the SEL rows or a fenced registry changed.

    Each planted row makes its change and does not undo it, and *probe* (a real row of the
    table) runs after it: once as every table runs, and, for the home and the SEL rows, once more
    without the reset that puts them back, so the check that guards the next row is what is seen.
    A fenced registry has no reset here: the conftest puts it back once per test, so the planted
    row and the probe are caught by the reading around them. Returns, per kind and per run
    (``"reset"``, ``"no reset"``), the rows that failed and their messages.
    """
    from personalclaw.tool_providers import registry as tools

    async def leaves_a_grant(r: Row) -> None:
        app = r.home / "apps" / shared.name
        (app / "planted-grant.json").write_text("{}", encoding="utf-8")
        # And a change to each of two files the install wrote, which the reset writes back.
        with (app / "installed.json").open("a", encoding="utf-8") as installed:
            installed.write("\n")
        (app / "app.json").unlink()

    async def leaves_a_row(r: Row) -> None:
        from personalclaw import sel

        sel.sel().log_api_access(caller=f"app:{shared.name}", outcome="denied", resources="/")

    async def leaves_a_registry(r: Row) -> None:
        tools._providers["planted-provider"] = object()

    plants = {"home": leaves_a_grant, "sel": leaves_a_row, "fenced": leaves_a_registry}
    found: dict[str, dict[str, dict[str, str]]] = {}
    for kind, plant in plants.items():
        found[kind] = {}
        runs = {"reset": shared.resets}
        if kind in shared.resets:
            runs["no reset"] = shared.resets - {kind}
        for run, resets in runs.items():
            recorded = RecordedRows()
            kept = shared.resets
            shared.restore()
            shared.resets = resets
            try:
                await shared.run(recorded, [(f"planted {kind}", plant), probe], routes)
            finally:
                shared.resets = kept
                tools._providers.pop("planted-provider", None)
            found[kind][run] = recorded.failed
    return found
