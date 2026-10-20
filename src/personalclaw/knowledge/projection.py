"""Deterministic 2-D layout for the knowledge graph — PCA by power iteration + deflation.

The graph canvas needs a position per item, and it needs the SAME position every session:
a layout that reshuffles on each load teaches the user nothing, because the only thing they
can learn from it (this cluster is over there, those two items sit together) is exactly what
a re-randomized layout destroys. So positions are computed HERE, server-side, from the item
embeddings, rather than by a force simulation in the browser — a force layout seeded from
``Math.random`` is the defect this module exists to prevent.

The projection is the first two principal components of the centred item matrix, found by
power iteration on the covariance operator with **Hotelling deflation** between components
(component 2 is extracted from the residual left after component 1 is subtracted from the
data, so the two axes cannot collapse onto the same direction). The iterate is initialised
from a ``random.Random(seed)`` stream — never the global RNG — so the whole result, including
each axis's sign, is a pure function of ``(vectors, seed)``.

Scaling is **isotropic**: both axes are divided by the single largest absolute coordinate, so
the output is a similarity transform of the PC1/PC2 plane and on-screen distance stays
proportional to distance in embedding space. Scaling the axes independently would fill the
viewport more evenly but would stretch a low-variance PC2 up to the same visual weight as
PC1 — inventing spread the data does not have, and breaking the one property the layout is
for.

Pure + unit-testable: no DB, no embedder, no I/O. Callers pass vectors already decoded from
their SQLite BLOBs via ``embedder.bytes_to_floats``.

Uses numpy (an unconditional core dependency) and does NOT guard the import: measured at
2000 items x 768 dims a pure-Python power iteration costs ~9.2s, which is not a thing a
payload handler can do. Unlike ``vector_memory.py``, there is no stdlib degradation to fall
back to — without a projection there is no layout at all.
"""

from __future__ import annotations

from collections import Counter
from random import Random

import numpy as np

# Layouts are stable across sessions only if every caller lands on the same seed, so the
# default is a constant here rather than at each call site.
DEFAULT_SEED = 20260817

# Nodes we cannot place: a vector of another dimension (a library part-way through a
# re-embed under a new model), or one that decoded to NaN/inf from a damaged BLOB. They are
# returned AT THE ORIGIN, never dropped — a missing node is an invisible data-loss bug,
# while a pile at the centre is a visible "unplaceable" state the canvas can label.
ORIGIN: tuple[float, float] = (0.0, 0.0)

_MAX_ITERS = 100  # bounds worst-case latency; real embedding data converges in ~10-40
_TOL = 1e-9  # iterate movement below this ⇒ converged (matrix is pre-normalized, see below)
_NO_VARIANCE = 1e-12  # operator norm below this ⇒ this direction carries no variance


def project_2d(
    vectors: dict[str, list[float]], *, seed: int = DEFAULT_SEED
) -> dict[str, tuple[float, float]]:
    """Map each id's embedding to a normalized 2-D point. Deterministic for a given input.

    Returns one ``(x, y)`` for EVERY input id, in input order, with both coordinates in
    ``[-1.0, 1.0]`` and never NaN/inf. The scaling is isotropic (a single factor for both
    axes), so at least one coordinate touches ±1 whenever the items have any variance, and
    relative distances are meaningful; the origin is the centroid of the placed items.

    ``vectors`` maps item id → decoded embedding (``embedder.bytes_to_floats`` output). The
    projection basis is the most common vector length — the ACTIVE model's dimension. Any
    vector of a different length, or holding a non-finite value, is placed at ``ORIGIN``.

    Degenerate inputs yield origins rather than raising or emitting NaN: an empty dict → an
    empty dict; every vector empty → all origin; fewer than two placeable vectors → all
    origin (a single point has no variance to spread); all-identical or all-zero vectors →
    all origin (zero variance, and the naive rescale there would divide by zero). Rank-1
    input (e.g. exactly two distinct vectors) spreads along x with y=0, which is the honest
    answer — there is no second component to show.

    ``seed`` fixes the iterate's initial direction and therefore each axis's SIGN: the same
    seed reproduces a layout down to its orientation, while another seed may mirror it. The
    geometry (pairwise distances) is seed-invariant once the iteration converges.
    """
    if not vectors:
        return {}

    # Seeded from the start so the caller's `seed` is the only source of variation.
    points: dict[str, tuple[float, float]] = {item_id: ORIGIN for item_id in vectors}
    dim = _basis_dim(vectors)
    if dim == 0:
        return points

    candidates = [item_id for item_id, vec in vectors.items() if len(vec) == dim]
    matrix = np.array([vectors[item_id] for item_id in candidates], dtype=np.float64)
    # Row-wise finiteness via numpy, not `all(math.isfinite(...))` per element — the latter
    # is ~1.5M Python-level calls on a 2000x768 library.
    finite = np.isfinite(matrix).all(axis=1)
    placeable = [item_id for item_id, ok in zip(candidates, finite) if ok]
    if len(placeable) < 2:
        return points
    matrix = matrix[finite]

    matrix -= matrix.mean(axis=0)
    # Normalize the centred matrix to unit Frobenius norm so _TOL and _NO_VARIANCE are
    # scale-free thresholds rather than assumptions about embedding magnitude. Harmless to
    # the result: the output is isotropically rescaled below anyway.
    frobenius = float(np.linalg.norm(matrix))
    if frobenius <= 0.0:
        return points  # every placeable vector identical ⇒ centred to exactly zero
    matrix /= frobenius

    rng = Random(seed)
    xs = _component(matrix, rng)  # deflates `matrix` in place
    ys = _component(matrix, rng)  # ⇒ this reads the residual, so ys ⊥ xs

    extent = max(float(np.abs(xs).max()), float(np.abs(ys).max()))
    if extent <= 0.0:
        return points
    xs = xs / extent
    ys = ys / extent

    for item_id, x, y in zip(placeable, xs, ys):
        # Last-resort floor on the module's no-NaN promise. The input filter above is the
        # real defence; this catches nothing today but costs nothing to keep honest.
        point = (float(x), float(y))
        points[item_id] = point if all(np.isfinite(point)) else ORIGIN
    return points


def _basis_dim(vectors: dict[str, list[float]]) -> int:
    """The active embedding dimension = the most common non-empty vector length. 0 when
    every vector is empty. Ties break on the larger dimension so the basis never depends on
    dict iteration order."""
    counts = Counter(len(vec) for vec in vectors.values() if vec)
    if not counts:
        return 0
    return max(counts, key=lambda dim: (counts[dim], dim))


def _component(matrix: np.ndarray, rng: Random) -> np.ndarray:
    """One principal component's scores, then DEFLATE ``matrix`` in place by it.

    Power-iterates ``v ← XᵀXv`` (the centred covariance, never materialised as a d×d matrix)
    from a seeded init, returns the scores ``Xv``, and subtracts that rank-1 component from
    the data. The subtraction is what makes a second call return a different, orthogonal
    direction instead of re-converging on the dominant one.

    Zero (remaining) variance yields zeros rather than a divide-by-zero: for rank-1 input the
    residual after the first call is numerically empty, and the second component is honestly
    absent.
    """
    rows, dim = matrix.shape
    # rng.gauss, not rng.uniform: an isotropic init has no axis bias, so no input direction
    # is systematically slow to converge.
    v = np.array([rng.gauss(0.0, 1.0) for _ in range(dim)], dtype=np.float64)
    norm = float(np.linalg.norm(v))
    if norm <= 0.0:
        return np.zeros(rows)
    v /= norm

    # numpy 1.26 + this BLAS emit spurious divide/overflow/invalid RuntimeWarnings from
    # matmul's SIMD kernels: a plain `A @ v` with every entry ~10 raises all three while
    # returning fully finite output, because numpy reports the CPU's accumulated FP status
    # flags rather than a real bad value. Suppressed here, not globally, and safe to
    # suppress in THIS scope specifically: `matrix` is unit-Frobenius and `v` is a unit
    # vector, so |matrix @ v| <= 1 and no genuine overflow or division is reachable. The
    # caller's isfinite check stays as the backstop. Without this the graph payload logs
    # three warnings per request.
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        for _ in range(_MAX_ITERS):
            nxt = matrix.T @ (matrix @ v)
            norm = float(np.linalg.norm(nxt))
            if norm <= _NO_VARIANCE:
                return np.zeros(rows)
            nxt /= norm
            converged = float(np.linalg.norm(nxt - v)) <= _TOL
            v = nxt
            if converged:
                break

        scores = matrix @ v
        matrix -= np.outer(scores, v)
    return scores
