"""Tests for the knowledge graph's server-side 2-D projection.

The properties under test are the ones the graph canvas depends on and that a reader of the
output cannot check for themselves: the layout is reproducible across sessions, no item is
ever silently dropped, an unplaceable vector is visibly at the origin instead of at NaN, and
distance on screen means distance in embedding space.

Every exclusion assertion here carries a positive control in the same test — an empty or
all-origin result must never be able to satisfy "these points are not at the origin".
"""

from __future__ import annotations

import math
import random

import pytest

from personalclaw.knowledge.projection import ORIGIN, project_2d

DIM = 16


def _cluster(prefix: str, centre: float, count: int, rng: random.Random) -> dict[str, list[float]]:
    """`count` jittered vectors around a constant-`centre` point. Jitter is per-dimension so
    the input has rank > 2 and a genuine second component to find."""
    return {
        f"{prefix}{i}": [centre + rng.gauss(0.0, 0.05) for _ in range(DIM)] for i in range(count)
    }


def _two_clusters(seed: int = 7) -> dict[str, list[float]]:
    rng = random.Random(seed)
    return {**_cluster("a", 1.0, 6, rng), **_cluster("b", -1.0, 6, rng)}


def _spread(vectors: dict[str, list[float]]) -> dict[str, tuple[float, float]]:
    """A projection whose points are known to be spread out — the positive control shared by
    the exclusion tests below."""
    return project_2d(vectors)


def _distances(points: dict[str, tuple[float, float]]) -> dict[tuple[str, str], float]:
    ids = sorted(points)
    return {
        (a, b): math.dist(points[a], points[b]) for i, a in enumerate(ids) for b in ids[i + 1 :]
    }


# ---------- determinism ----------


def test_same_input_twice_is_byte_identical() -> None:
    """The whole point of the clause: a layout that reshuffles between sessions teaches the
    user nothing. Compares floats with `==` deliberately — this must be bit-exact, not close."""
    vectors = _two_clusters()

    first = project_2d(vectors)
    second = project_2d(vectors)

    assert first == second
    # Vacuity guard: an all-origin result would compare equal trivially.
    assert any(point != ORIGIN for point in first.values())


def test_seed_is_load_bearing_and_geometry_is_seed_invariant() -> None:
    """A different seed may mirror or rotate the layout (the iterate's initial direction fixes
    each axis's sign), but it must not change the SHAPE: pairwise distances are what carry
    semantic meaning, so they survive a seed change."""
    vectors = _two_clusters()

    default = project_2d(vectors)
    other = project_2d(vectors, seed=99)

    assert set(other) == set(vectors)
    for point in other.values():
        assert all(math.isfinite(c) and abs(c) <= 1.0 + 1e-9 for c in point)

    base_d = _distances(default)
    other_d = _distances(other)
    assert base_d.keys() == other_d.keys()
    assert any(value > 0.1 for value in base_d.values())  # vacuity: distances are non-trivial
    for pair, distance in base_d.items():
        assert other_d[pair] == pytest.approx(distance, abs=1e-6), pair


def test_seeding_is_not_read_from_global_random_state() -> None:
    """Seeding the global `random` module differently between calls must not move the layout —
    the iterate comes from a private `Random(seed)` stream, never `random.random()`."""
    vectors = _two_clusters()

    random.seed(1)
    first = project_2d(vectors)
    random.seed(9999)
    second = project_2d(vectors)

    assert first == second
    assert any(point != ORIGIN for point in first.values())


# ---------- completeness ----------


def test_every_input_id_is_present_in_the_output() -> None:
    """A dropped node is invisible data loss on the canvas, so the payload must stay complete
    even when some vectors are unusable."""
    vectors = {
        **_two_clusters(),
        "wrong_dim": [1.0] * (DIM + 3),
        "empty": [],
        "nan": [float("nan")] * DIM,
    }

    points = project_2d(vectors)

    assert list(points) == list(vectors)  # same ids, same order
    assert len(points) == len(vectors)


# ---------- unplaceable vectors ----------


def test_mixed_dimension_vectors_land_exactly_at_the_origin() -> None:
    """A library part-way through a re-embed under a new model holds two vector lengths. The
    minority length is unplaceable — it is not comparable to the active basis — and goes to the
    origin, while the majority length is projected normally."""
    active = _two_clusters()
    stale = {f"stale{i}": [0.5 + i] * (DIM // 2) for i in range(3)}

    points = project_2d({**active, **stale})

    for item_id in stale:
        assert points[item_id] == ORIGIN, item_id
    # Vacuity guard: the same-dimension items must NOT be at the origin, or "at the origin"
    # above would be satisfied by a function that origins everything.
    placed = [points[item_id] for item_id in active]
    assert all(point != ORIGIN for point in placed)
    assert max(math.dist(p, ORIGIN) for p in placed) > 0.5


def test_majority_dimension_wins_the_basis() -> None:
    """The basis is the ACTIVE model's dimension, i.e. the most common one — not whichever
    length happens to be seen first."""
    rng = random.Random(3)
    minority = {f"m{i}": [rng.gauss(0, 1) for _ in range(DIM * 2)] for i in range(2)}
    majority = _two_clusters()

    # Minority inserted FIRST: a first-seen basis would pick its length and invert the result.
    points = project_2d({**minority, **majority})

    for item_id in minority:
        assert points[item_id] == ORIGIN, item_id
    assert all(points[item_id] != ORIGIN for item_id in majority)


def test_non_finite_vectors_land_at_the_origin_without_poisoning_the_layout() -> None:
    """A damaged BLOB decodes to NaN/inf through `struct.unpack`. One such row must not
    propagate NaN into every other node's coordinates via the shared mean and covariance."""
    healthy = _two_clusters()
    broken = {
        "nan": [float("nan")] + [0.1] * (DIM - 1),
        "inf": [float("inf")] + [0.1] * (DIM - 1),
        "neg_inf": [float("-inf")] + [0.1] * (DIM - 1),
    }

    points = project_2d({**healthy, **broken})

    for item_id in broken:
        assert points[item_id] == ORIGIN, item_id
    for item_id in healthy:
        x, y = points[item_id]
        assert math.isfinite(x) and math.isfinite(y), item_id
        assert points[item_id] != ORIGIN, item_id


# ---------- degenerate inputs ----------

DEGENERATE: dict[str, dict[str, list[float]]] = {
    "empty_dict": {},
    "single_vector": {"only": [1.0, 2.0, 3.0]},
    "two_identical": {"a": [1.0, 2.0], "b": [1.0, 2.0]},
    "all_identical": {f"i{i}": [0.5, 0.5, 0.5] for i in range(5)},
    "all_zero": {f"z{i}": [0.0, 0.0, 0.0] for i in range(4)},
    "all_empty_vectors": {"a": [], "b": [], "c": []},
    "every_vector_a_different_length": {"a": [1.0], "b": [1.0, 2.0], "c": [1.0, 2.0, 3.0]},
    "rank_one": {"a": [1.0, 1.0], "b": [2.0, 2.0], "c": [3.0, 3.0]},
    "one_huge_one_tiny": {"a": [1e18] * 4, "b": [1e-18] * 4, "c": [0.0] * 4},
}


@pytest.mark.parametrize("name", sorted(DEGENERATE))
def test_degenerate_input_never_yields_nan_or_inf(name: str) -> None:
    """A NaN coordinate renders as a node jammed in a corner or not at all — a layout bug that
    looks permanent. Zero-variance input is the dangerous family: the covariance is singular
    and a naive rescale divides by zero."""
    vectors = DEGENERATE[name]

    points = project_2d(vectors)

    assert set(points) == set(vectors), name
    for item_id, (x, y) in points.items():
        assert math.isfinite(x) and math.isfinite(y), f"{name}/{item_id} -> {(x, y)}"
        assert abs(x) <= 1.0 + 1e-9 and abs(y) <= 1.0 + 1e-9, f"{name}/{item_id} -> {(x, y)}"

    # Vacuity guard: prove in this same test that the finiteness assertion above can
    # discriminate at all, i.e. a healthy input does produce placed, non-origin points.
    control = _spread(_two_clusters())
    assert any(point != ORIGIN for point in control.values())


def test_zero_variance_input_collapses_to_the_origin_rather_than_inventing_spread() -> None:
    """All-identical items have no relative position to show. Scaling numerical noise up to
    fill the canvas would fabricate structure, so they collapse to the centroid."""
    identical = {f"same{i}": [0.25] * DIM for i in range(6)}

    points = project_2d(identical)

    assert all(point == ORIGIN for point in points.values())
    # Vacuity guard: the SAME shape of input with real variance must not collapse.
    assert any(point != ORIGIN for point in project_2d(_two_clusters()).values())


# ---------- the semantic property ----------


def test_clusters_separate_further_than_their_own_spread() -> None:
    """ "On-screen distance carries semantic meaning": two groups that are far apart in
    embedding space must be visibly further apart on the canvas than either group is wide."""
    vectors = _two_clusters()
    points = project_2d(vectors)
    group_a = [points[f"a{i}"] for i in range(6)]
    group_b = [points[f"b{i}"] for i in range(6)]

    def centroid(group: list[tuple[float, float]]) -> tuple[float, float]:
        return (sum(p[0] for p in group) / len(group), sum(p[1] for p in group) / len(group))

    centre_a, centre_b = centroid(group_a), centroid(group_b)
    between = math.dist(centre_a, centre_b)
    within = max(
        max(math.dist(p, centre_a) for p in group_a),
        max(math.dist(p, centre_b) for p in group_b),
    )

    assert within > 0.0  # vacuity: the clusters are not single collapsed points
    assert between > 3 * within, f"between={between} within={within}"


def test_deflation_gives_the_two_axes_independent_information() -> None:
    """Without deflation both power iterations converge on the dominant eigenvector and y
    becomes a copy of x — a diagonal line, not a map. The axes must be near-uncorrelated."""
    rng = random.Random(11)
    vectors = {f"i{i}": [rng.gauss(0.0, 1.0) for _ in range(24)] for i in range(50)}

    points = project_2d(vectors)
    xs = [x for x, _ in points.values()]
    ys = [y for _, y in points.values()]

    dot = sum(x * y for x, y in zip(xs, ys))
    norm_x = math.sqrt(sum(x * x for x in xs))
    norm_y = math.sqrt(sum(y * y for y in ys))
    assert norm_x > 0.0 and norm_y > 0.0  # vacuity: neither axis is a flat zero
    assert abs(dot / (norm_x * norm_y)) < 0.01, "axes are correlated ⇒ deflation is not working"


# ---------- output range ----------


def test_output_is_normalized_to_the_documented_range() -> None:
    """The docstring promises [-1, 1] on both axes, and promises the extent is actually USED —
    a client that scales the range to its viewport must not get a layout crammed near zero."""
    rng = random.Random(5)
    vectors = {f"i{i}": [rng.gauss(0.0, 300.0) for _ in range(20)] for i in range(40)}

    points = project_2d(vectors)
    coords = [c for point in points.values() for c in point]

    assert all(abs(c) <= 1.0 + 1e-9 for c in coords)
    assert max(abs(c) for c in coords) == pytest.approx(1.0, abs=1e-9)


def test_scaling_is_isotropic_so_input_magnitude_does_not_change_the_layout() -> None:
    """Multiplying every embedding by a constant is not a semantic change, so the layout must
    be identical. This is what an isotropic (single-factor) rescale buys."""
    vectors = _two_clusters()
    scaled = {item_id: [c * 1000.0 for c in vec] for item_id, vec in vectors.items()}

    base = project_2d(vectors)
    big = project_2d(scaled)

    assert any(point != ORIGIN for point in base.values())
    for item_id, point in base.items():
        assert big[item_id][0] == pytest.approx(point[0], abs=1e-6), item_id
        assert big[item_id][1] == pytest.approx(point[1], abs=1e-6), item_id
