"""On-disk layout probing for downloaded local models (LOCAL-MODEL-MANAGER-V2 §4.4).

**The bug class this exists to end.** A downloaded model can land on disk in several shapes
depending on who fetched it, and every consumer was guessing at one of them:

* **HF hub snapshot** — `models--{org}--{name}/snapshots/{rev}/…`, with the real bytes in
  `blobs/` and symlinks in the snapshot dir. This is what `huggingface_hub` produces, and the
  one most often guessed wrong because the model id (`org/name`) does not appear literally.
* **Provider-native** — a directory or file named after the model, which a provider's own
  `save()` wrote.
* **Direct file** — a single artifact (`.onnx`, `.gguf`, a piper voice) fetched by URL, where
  the model id maps to a filename possibly with an extension.

Guessing one layout gives a `downloaded` flag that reads False for a model sitting right
there — the user re-downloads gigabytes — or a `delete` that reports success while leaving the
weights behind, so the disk never frees. Both were observed, which is why the plan makes this
ONE shared helper that probes **every** layout rather than per-provider guesses.

**A partial download is NOT downloaded.** `models--…/blobs` holding only `*.incomplete`, or a
bare `<name>.part`, means an interrupted fetch. Reporting that as present is worse than
reporting nothing: the user gets a model that fails at load time with no explanation.

**Deleting is greedy on purpose.** `delete_all_layouts` removes every layout it finds and
returns what it touched, rather than stopping at the first hit. A model that was fetched twice
by different paths (a provider `save()` and later an HF snapshot) leaves two copies, and a
delete that frees one of them is the disk-never-frees bug in a different costume.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

#: Suffixes that mark an in-flight fetch rather than a finished artifact.
PARTIAL_SUFFIXES: tuple[str, ...] = (".part", ".tmp", ".incomplete", ".download")

#: Extensions a direct-file model may carry. A model id like "en_US-amy-medium" can land as
#: itself or with one of these appended, and the probe must not care which.
DIRECT_FILE_EXTENSIONS: tuple[str, ...] = (
    "",
    ".onnx",
    ".gguf",
    ".bin",
    ".safetensors",
    ".pt",
    ".pth",
    ".tar.gz",
)

#: A finished artifact must exceed this. A zero-byte file is a touched placeholder, not a
#: model, and treating it as downloaded is the same failure as counting a `.part`.
MIN_ARTIFACT_BYTES = 1


def hf_repo_dirname(model: str) -> str:
    """The `models--org--name` directory `huggingface_hub` uses for *model*.

    Mirrors the hub's own escaping (``/`` → ``--``) rather than importing it, so a probe
    keeps working when `huggingface_hub` isn't installed — which is exactly the headless case
    where a wrong answer costs the most.
    """
    return "models--" + str(model or "").strip().strip("/").replace("/", "--")


def _is_partial(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in PARTIAL_SUFFIXES)


def _has_real_bytes(path: Path) -> bool:
    """Whether *path* holds at least one finished, non-empty artifact."""
    try:
        if path.is_file():
            return not _is_partial(path) and path.stat().st_size >= MIN_ARTIFACT_BYTES
        if not path.is_dir():
            return False
        for child in path.rglob("*"):
            try:
                if child.is_file() and not _is_partial(child):
                    if child.stat().st_size >= MIN_ARTIFACT_BYTES:
                        return True
            except OSError:
                continue
        return False
    except OSError:
        logger.debug("layout probe: could not stat %s", path, exc_info=True)
        return False


def candidate_paths(cache_root: str | Path, model: str) -> list[Path]:
    """Every path *model* could occupy under *cache_root*, in probe order.

    Returned whether or not they exist — callers use this both to look for a download and to
    know what a delete must sweep.
    """
    root = Path(cache_root)
    name = str(model or "").strip().strip("/")
    if not name:
        return []

    out: list[Path] = [root / hf_repo_dirname(name)]

    # Provider-native: the model id as a path. A slashed id nests, which is what a provider's
    # own save() does when it mirrors the upstream namespace.
    out.append(root / name)

    # Direct file: the LAST path segment plus each known extension. "org/model.onnx" and
    # "model.onnx" must probe the same filename.
    leaf = name.rsplit("/", 1)[-1]
    for ext in DIRECT_FILE_EXTENSIONS:
        candidate = root / f"{leaf}{ext}"
        if candidate not in out:
            out.append(candidate)

    # Ollama-style: a colon-tagged id whose tag is not part of the path.
    if ":" in leaf:
        base = leaf.split(":", 1)[0]
        candidate = root / base
        if candidate not in out:
            out.append(candidate)

    # Partial artifacts for the direct-file layouts. `is_downloaded` rejects these (a partial
    # is not a download), but `delete_all_layouts` must still SWEEP them — otherwise a
    # cancelled fetch leaves a `.part` that nothing lists and nothing removes, which is the
    # invisible-leftover half of the same bug.
    for ext in DIRECT_FILE_EXTENSIONS:
        for suffix in PARTIAL_SUFFIXES:
            candidate = root / f"{leaf}{ext}{suffix}"
            if candidate not in out:
                out.append(candidate)
    return out


def is_downloaded(cache_root: str | Path, model: str) -> bool:
    """Whether *model* is present and COMPLETE under *cache_root*.

    True only when some layout holds at least one finished, non-empty file. A directory that
    exists but contains only partials is False — see the module note on why reporting a
    partial as present is worse than reporting nothing.
    """
    for path in candidate_paths(cache_root, model):
        if _has_real_bytes(path):
            return True
    return False


def downloaded_layouts(cache_root: str | Path, model: str) -> list[Path]:
    """Every layout that actually holds *model* — usually one, sometimes more.

    More than one means the model was fetched by two different paths. Surfaced rather than
    hidden, because it is the shape of a real disk leak.
    """
    return [p for p in candidate_paths(cache_root, model) if _has_real_bytes(p)]


def on_disk_bytes(cache_root: str | Path, model: str) -> int:
    """Total bytes *model* actually occupies across every layout that holds it.

    Sums `st_size` over the files in each :func:`downloaded_layouts` path — the number the
    §2.3 truncation detector compares against the card's expected `size_mb`. Zero when the
    model isn't present. Best-effort per file (an unreadable file contributes nothing)
    rather than raising, mirroring the fail-soft posture of the rest of this module.
    """
    total = 0
    for layout in downloaded_layouts(cache_root, model):
        try:
            if layout.is_file():
                total += layout.stat().st_size
                continue
            for child in layout.rglob("*"):
                try:
                    if child.is_file():
                        total += child.stat().st_size
                except OSError:
                    continue
        except OSError:
            logger.debug("on-disk size probe: could not stat %s", layout, exc_info=True)
    return total


def delete_all_layouts(cache_root: str | Path, model: str) -> list[Path]:
    """Delete every layout of *model*, returning what was removed.

    Greedy by design: a model fetched twice leaves two copies, and freeing one is the
    disk-never-frees bug wearing a different hat. Also removes partials, so a cancelled
    download doesn't linger invisibly.

    Never raises for a path it cannot remove — the removal is best-effort per path and the
    return value tells the caller what actually went.
    """
    removed: list[Path] = []
    for path in candidate_paths(cache_root, model):
        try:
            if not path.exists():
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(path)
        except OSError:
            logger.warning("could not delete model layout %s", path, exc_info=True)
    if removed:
        logger.info("deleted %d layout(s) for %r", len(removed), model)
    return removed


def cleanup_candidates(cache_root: str | Path) -> list[dict]:
    """Partial-download leftovers under *cache_root*, with sizes (§4.2).

    Feeds the "Reclaim N GB" affordance. Enumerates `*.part`/`*.tmp`/`*.incomplete` and the
    hub's own `*.incomplete` blobs — the files a cancelled or crashed fetch leaves behind,
    which are otherwise invisible because nothing lists them.
    """
    root = Path(cache_root)
    if not root.is_dir():
        return []
    out: list[dict] = []
    try:
        for path in root.rglob("*"):
            try:
                if path.is_file() and _is_partial(path):
                    out.append({"path": str(path), "bytes": path.stat().st_size})
            except OSError:
                continue
    except OSError:
        logger.debug("cleanup scan failed under %s", root, exc_info=True)
    out.sort(key=lambda d: d["bytes"], reverse=True)
    return out


def reclaimable_bytes(cache_root: str | Path) -> int:
    """Total size of the cleanup candidates — the number the UI puts on the button."""
    return sum(c["bytes"] for c in cleanup_candidates(cache_root))
