"""One local-model registry identity for a multi-instance app's N enabled instances.

A ``multiInstance: true`` model app's factory returns **one provider per enabled
instance** (``providers.registry.ModelTypeHandler.create``), while the local-model
registry is a flat ``name -> provider`` map keyed by the **app** name. Registering the
instances one at a time under that one key made the LAST one win and made tearing down
ONE instance remove the whole app's entry — so a healthy Ollama could read *"not
available on this machine"* because a sibling registered after it, and deleting one of
two endpoints stranded the survivor on the download, health and binding surfaces
(issue #3410).

**Why a longer key is the wrong fix.** The registry key is not a private handle: it is a
path segment on five routes (``/api/models/local/{provider}/…``), a body field on two,
and the provider half of every ``provider:model`` binding ref in ``active_models.json``.
``use_cases.split_ref`` splits a ref on the **first** colon, so an ``app:instance`` key
cannot round-trip through one — and ``use_cases._prune_removed_providers`` keeps a ref
only when its first-colon prefix is a known provider name, so the moment the app name
stopped being a key it would **silently delete every existing binding** for that app.
Trading a lost registry entry for deleted user bindings is not a fix.

**So the app keeps exactly one identity, and this object IS that identity.** Every answer
is a pure function of ALL enabled instances, taken in ``list_instances`` order:
availability is "any instance can serve", the catalog is the union, and a download or a
delete fans out to every instance rather than picking one. That removes the question the
old code answered nondeterministically — there is no "which instance won" left to decide,
so nothing here reads dict order, a clock, or a random id. And because the multiplicity
is no longer invisible, :meth:`availability_detail` names each instance and its state,
which is the surface that was missing when adding a second instance could silently
repoint the provider at a different endpoint.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from personalclaw.local_models.provider import LocalModel, LocalModelProvider

logger = logging.getLogger(__name__)


def instance_identity(provider: object, *, fallback: str) -> tuple[str, str]:
    """``(instance_id, label)`` for one instance's provider, as ``create()`` tagged it.

    ``ModelTypeHandler.create`` stamps ``instance_id`` / ``instance_label`` on every
    provider it builds for a multiInstance app — core's own bookkeeping, deliberately not
    read off ``provider.name``, which a provider may expose as a read-only property that
    is identical for every instance (the real Ollama app does exactly that, which is why
    the per-instance tag ``create()`` appears to apply has never applied).
    """
    iid = str(getattr(provider, "instance_id", "") or "") or fallback
    return iid, str(getattr(provider, "instance_label", "") or "") or iid


class MultiInstanceLocalProvider(LocalModelProvider):
    """The local-model registry entry for a multiInstance app, over its instances.

    Constructed from ``(instance_id, label, provider)`` triples in the order
    ``providers.instances.list_instances`` yielded them. Immutable: :meth:`without`
    returns a new aggregate, so a teardown of some instances can never mutate the entry
    another caller is already holding.
    """

    def __init__(self, app_name: str, members: list[tuple[str, str, Any]]) -> None:
        if not members:
            raise ValueError("a multi-instance local provider needs at least one instance")
        self._app_name = app_name
        self._members: tuple[tuple[str, str, Any], ...] = tuple(members)
        # Any instance with a dynamic remote catalog makes the app searchable.
        self.searchable = any(
            bool(getattr(p, "searchable", False)) for _iid, _label, p in self._members
        )

    # ── identity ──

    @property
    def name(self) -> str:
        return self._app_name

    @property
    def display_name(self) -> str:
        """The instances' shared label, with the multiplicity spelled out.

        A two-endpoint Ollama used to render as a single card labelled "Ollama" with no
        hint that a second endpoint existed at all.
        """
        labels = {str(getattr(p, "display_name", "") or "") for _iid, _label, p in self._members}
        base = labels.pop() if len(labels) == 1 else self._app_name
        base = base or self._app_name
        if len(self._members) == 1:
            return base
        return f"{base} ({len(self._members)} instances)"

    @property
    def instances(self) -> tuple[tuple[str, str, Any], ...]:
        """The ``(instance_id, label, provider)`` triples this entry answers for."""
        return self._members

    @property
    def instance_ids(self) -> tuple[str, ...]:
        return tuple(iid for iid, _label, _p in self._members)

    def without(self, instance_ids: set[str]) -> "MultiInstanceLocalProvider | None":
        """This aggregate minus ``instance_ids`` — ``None`` when nothing would remain.

        ``None`` is the only case in which the app leaves the registry, which is what
        keeps a teardown of one instance from stranding its siblings.
        """
        kept = [m for m in self._members if m[0] not in instance_ids]
        if not kept:
            return None
        if len(kept) == len(self._members):
            return self
        return MultiInstanceLocalProvider(self._app_name, kept)

    # ── availability ──

    async def _gather(self, coros: list[Any]) -> list[Any]:
        """Run per-instance work concurrently; an instance's failure is its own result."""
        return list(await asyncio.gather(*coros, return_exceptions=True))

    async def is_available(self) -> bool:
        """True when ANY enabled instance can serve.

        The whole point: one dead endpoint must not make a healthy sibling read as
        "not available on this machine".
        """
        results = await self._gather([self._one_available(p) for _iid, _label, p in self._members])
        return any(r is True for r in results)

    @staticmethod
    async def _one_available(provider: Any) -> bool:
        try:
            return bool(await provider.is_available())
        except Exception:  # noqa: BLE001 — one unreachable instance is not an app-wide crash
            return False

    async def availability_detail(self) -> tuple[bool, str]:
        """``(any_available, per-instance message)`` — the health route's source of truth.

        The message is where the multiplicity becomes visible: every instance is named
        with its own verdict, in instance order, so "2 instances, 1 ready" can no longer
        render as a flat red or a flat green that describes only one endpoint.
        """
        details = await self._gather([self._one_detail(p) for _iid, _label, p in self._members])
        parts: list[str] = []
        ok_count = 0
        for (_iid, label, _p), got in zip(self._members, details, strict=True):
            if isinstance(got, tuple):
                ok, message = bool(got[0]), str(got[1])
            else:
                ok, message = False, f"{type(got).__name__}: {got}"
            ok_count += 1 if ok else 0
            parts.append(f"{label}: {message}")
        total = len(self._members)
        if total == 1:
            return ok_count == 1, parts[0].split(": ", 1)[-1]
        head = f"{ok_count} of {total} instances ready"
        return ok_count > 0, f"{head} — " + "; ".join(parts)

    @staticmethod
    async def _one_detail(provider: Any) -> tuple[bool, str]:
        detail = getattr(provider, "availability_detail", None)
        try:
            if callable(detail):
                ok, message = await detail()
                return bool(ok), str(message)
            ok = bool(await provider.is_available())
        except Exception as exc:  # noqa: BLE001 — health never 500s (LMMV §6)
            return False, f"{type(exc).__name__}: {exc}"
        return ok, "ready" if ok else "not available on this machine"

    async def ensure_ready(self) -> tuple[bool, str]:
        """``ready`` when any instance is ready, else ``loading`` if any is warming."""
        states = await self._gather([self._one_state(p) for _iid, _label, p in self._members])
        pairs = [s for s in states if isinstance(s, tuple)]
        if any(ok for ok, _state in pairs):
            return True, "ready"
        if any(state == "loading" for _ok, state in pairs):
            return False, "loading"
        return False, "unavailable"

    @staticmethod
    async def _one_state(provider: Any) -> tuple[bool, str]:
        ready = getattr(provider, "ensure_ready", None)
        try:
            if callable(ready):
                ok, state = await ready()
                return bool(ok), str(state)
            ok = bool(await provider.is_available())
        except Exception:  # noqa: BLE001 — an unreachable instance is simply not ready
            return False, "unavailable"
        return ok, "ready" if ok else "unavailable"

    # ── catalog ──

    async def list_models(self) -> list[LocalModel]:
        """The union of the instances' catalogs, deduped by model name.

        Order is instance order then each instance's own order, so two runs over the same
        instances produce the same list. ``downloaded`` is True only when every instance
        that answered has it — a model present on one endpoint of two is not "downloaded
        for this provider", and treating it as such is what would make a fan-out download
        look like a no-op.
        """
        return await self._merged("list_models")

    async def search_models(self, query: str) -> list[LocalModel]:
        """The union of the instances' remote-catalog searches, deduped by name."""
        return await self._merged("search_models", query)

    async def _merged(self, method: str, *args: Any) -> list[LocalModel]:
        results = await self._gather(
            [self._one_catalog(p, method, *args) for _iid, _label, p in self._members]
        )
        catalogs = [r for r in results if isinstance(r, list)]
        merged: dict[str, LocalModel] = {}
        # An instance whose catalog could not be read does not get a vote on `downloaded`.
        seen_by: dict[str, int] = {}
        downloaded_by: dict[str, int] = {}
        for catalog in catalogs:
            for model in catalog:
                key = str(getattr(model, "name", "") or "")
                if not key:
                    continue
                seen_by[key] = seen_by.get(key, 0) + 1
                if getattr(model, "downloaded", False):
                    downloaded_by[key] = downloaded_by.get(key, 0) + 1
                merged.setdefault(key, model)
        answering = len(catalogs)
        for key, model in merged.items():
            # Present everywhere that answered → downloaded. Otherwise it is partial, and
            # the download surface must keep offering it.
            model.downloaded = answering > 0 and downloaded_by.get(key, 0) >= answering
        return list(merged.values())

    @staticmethod
    async def _one_catalog(provider: Any, method: str, *args: Any) -> list[LocalModel] | None:
        """That instance's catalog, or ``None`` when it could not answer.

        ``None`` and ``[]`` are deliberately different: an instance that is unreachable
        must not cast a "this model is missing here" vote, while one that genuinely holds
        nothing must.
        """
        fn = getattr(provider, method, None)
        if not callable(fn):
            return None
        try:
            return list(await fn(*args))
        except Exception:  # noqa: BLE001 — one instance's catalog must not blank the card
            logger.debug("%s failed for an instance of %s", method, provider, exc_info=True)
            return None

    # ── writes: fan out, never guess ──

    async def download_model(self, model_name: str) -> bool:
        """Fetch ``model_name`` on EVERY instance. True when every one that ran succeeded.

        Fanning out is what makes the app's one identity honest: a binding that names this
        provider must resolve wherever the provider runs, and picking one instance to pull
        onto would be the arbitrary choice this class exists to remove.
        """
        return await self._fan_out("download_model", model_name)

    async def delete_model(self, model_name: str) -> bool:
        """Remove ``model_name`` from EVERY instance, symmetrically with the download."""
        return await self._fan_out("delete_model", model_name)

    async def _fan_out(self, method: str, model_name: str) -> bool:
        results = await self._gather(
            [self._one_write(p, method, model_name) for _iid, _label, p in self._members]
        )
        for (_iid, label, _p), got in zip(self._members, results, strict=True):
            if isinstance(got, BaseException):
                # A refusal (LiveWriteDisabled) or a backend error must not be reported as
                # a partial success, and must name the instance it came from.
                logger.warning("%s on instance %s of %s failed", method, label, self._app_name)
                raise got
        return all(r is True for r in results)

    @staticmethod
    async def _one_write(provider: Any, method: str, model_name: str) -> bool:
        return bool(await getattr(provider, method)(model_name))

    # ── residency ──

    def cache_dir(self) -> str | None:
        """The instances' shared cache dir, or ``None`` when they disagree.

        ``None`` degrades the download progress bar to indeterminate, which is the honest
        answer when two instances keep their weights in different places.
        """
        dirs = set()
        for _iid, _label, provider in self._members:
            getter = getattr(provider, "cache_dir", None)
            if not callable(getter):
                return None
            try:
                dirs.add(getter() or None)
            except Exception:  # noqa: BLE001 — a cache hint must never break a listing
                return None
        return dirs.pop() if len(dirs) == 1 else None

    def loaded_models(self) -> list[dict[str, Any]]:
        """Every instance's resident models, each row labelled with its instance."""
        rows: list[dict[str, Any]] = []
        for _iid, label, provider in self._members:
            reporter = getattr(provider, "loaded_models", None)
            if not callable(reporter):
                continue
            try:
                got = reporter() or []
            except Exception:  # noqa: BLE001 — an unreadable instance holds nothing visible
                continue
            for row in got:
                merged = dict(row)
                merged["instance"] = label
                rows.append(merged)
        return rows

    def unload(self) -> bool:
        """Release resident models on every instance. True if any freed something."""
        freed = False
        for _iid, _label, provider in self._members:
            unloader = getattr(provider, "unload", None)
            if not callable(unloader):
                continue
            try:
                freed = bool(unloader()) or freed
            except Exception:  # noqa: BLE001 — one instance that cannot free is not a failure
                logger.debug("unload failed for an instance of %s", self._app_name, exc_info=True)
        return freed
