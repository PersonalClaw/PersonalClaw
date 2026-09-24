"""Community topology over the memory entity graph — a deterministic seeded Louvain.

MEMORY-GRAPH-AND-VAULT §2.4 (MGAV-5). Entities that keep showing up in the same records
form neighbourhoods; naming those neighbourhoods lets a new session open with "here are
the areas I know about" instead of either nothing or a wall of facts.

Runs on the existing consolidation maintenance cadence in ``history.py`` — NOT a new
loop — writes ``community`` into ``mem_link_stats``, and materializes a ≤400-char
topology block that ``MemoryService.get_context`` injects on new sessions when
``memory.graph_topology_in_context`` is on (default off).

**Determinism is a hard requirement, not a nicety.** A community id that changes
between runs would silently rewrite the topology block, the visualization's colours and
the ``community`` column on every consolidation, and nothing would look broken. Three
things buy determinism here and all three are load-bearing:

1. The node list, adjacency lists and every iteration order are derived from **sorted**
   sequences, never from a ``set``/``dict`` insertion order.
2. The one place the algorithm wants randomness — the order nodes are visited during
   local moving — uses a fixed ``random.Random(LOUVAIN_SEED)``, seeded per call, so two
   processes (with different ``PYTHONHASHSEED``) agree.
3. Community LABELS are canonicalized at the end by (size desc, smallest member id),
   because Louvain's internal ids are an artifact of visit order and would otherwise
   permute between runs that found the identical partition.

The graph itself is built from co-occurrence: two entities linked from the SAME record
share an edge whose weight is the number of records they co-occur in. That is the only
relation ``mem_links`` actually asserts (record → entity), so deriving anything else
would be inventing structure.
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict

logger = logging.getLogger(__name__)

#: Fixed seed — llm-wiki-agent's ``seed=42`` discipline. Never read from config: a
#: user-tunable seed would make "same graph, same communities" untestable.
LOUVAIN_SEED = 42

#: Hard ceiling on the injected block (§2.4).
TOPOLOGY_BLOCK_MAX_CHARS = 400
#: Communities named in the block, and label entities per community.
TOPOLOGY_MAX_COMMUNITIES = 4
TOPOLOGY_LABELS_PER_COMMUNITY = 3

#: Local-moving passes per level. Louvain converges in a handful; the bound exists so a
#: pathological graph cannot spin the maintenance cadence.
_MAX_PASSES = 20
_MAX_LEVELS = 10


def cooccurrence_edges(db) -> dict[tuple[str, str], float]:
    """Undirected co-occurrence weights between entities, keyed by sorted id pair.

    Two entities are adjacent when at least one record links to both. Weight = the
    number of such records. Self-pairs are skipped (a record naming one entity twice
    says nothing about topology).
    """
    rows = db.execute(
        "SELECT from_kind, from_ref, to_entity FROM mem_links "
        "WHERE to_entity IS NOT NULL AND to_entity != '' "
        "ORDER BY from_kind, from_ref, to_entity"
    ).fetchall()
    per_record: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        entity = row["to_entity"] if not isinstance(row, tuple) else row[2]
        kind = row["from_kind"] if not isinstance(row, tuple) else row[0]
        ref = row["from_ref"] if not isinstance(row, tuple) else row[1]
        bucket = per_record[(kind, ref)]
        if entity not in bucket:
            bucket.append(entity)
    edges: dict[tuple[str, str], float] = {}
    for key in sorted(per_record):
        members = sorted(per_record[key])
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                edges[(a, b)] = edges.get((a, b), 0.0) + 1.0
    return edges


def _modularity_partition(
    nodes: list[str], edges: dict[tuple[str, str], float], rng: random.Random
) -> dict[str, int]:
    """Louvain's local-moving phase over an undirected weighted graph.

    Returns node → community id. ``edges`` may contain self-loops (``(a, a)``), which is
    how :func:`detect_communities` carries a collapsed community's internal weight into
    the next level — dropping them is the classic Louvain aggregation bug that makes every
    level merge until the whole graph is one community.

    Written out rather than pulled from ``networkx``/``python-louvain``: the memory store
    must not grow a scientific-Python dependency to draw four labels, and owning the loop
    is the only way to guarantee the iteration order this module's docstring promises.
    """
    if not nodes:
        return {}
    adjacency: dict[str, list[tuple[str, float]]] = {n: [] for n in nodes}
    self_loops: dict[str, float] = {n: 0.0 for n in nodes}
    total_weight = 0.0
    for (a, b), w in sorted(edges.items()):
        if a not in adjacency or b not in adjacency:
            continue
        total_weight += w
        if a == b:
            self_loops[a] += w
            continue
        adjacency[a].append((b, w))
        adjacency[b].append((a, w))
    for node in adjacency:
        adjacency[node].sort()

    # Every node starts in its own community; an isolated node stays there.
    community_of = {n: i for i, n in enumerate(sorted(nodes))}
    if total_weight <= 0:
        return community_of

    two_m = 2.0 * total_weight
    # A self-loop counts twice toward its node's degree, so sum(degree) == 2m holds.
    degree = {n: sum(w for _, w in adjacency[n]) + 2.0 * self_loops[n] for n in nodes}
    community_degree: dict[int, float] = defaultdict(float)
    for node, comm in community_of.items():
        community_degree[comm] += degree[node]

    order = sorted(nodes)
    rng.shuffle(order)
    for _ in range(_MAX_PASSES):
        moved = False
        for node in order:
            own = community_of[node]
            deg = degree[node]
            community_degree[own] -= deg
            # Weight from this node into each neighbouring community (self-loops excluded
            # — a node's internal weight follows it wherever it goes and cannot prefer
            # one destination over another).
            links: dict[int, float] = defaultdict(float)
            for neighbour, w in adjacency[node]:
                links[community_of[neighbour]] += w
            best_comm, best_gain = own, links.get(own, 0.0) - community_degree[own] * deg / two_m
            for comm in sorted(links):
                gain = links[comm] - community_degree[comm] * deg / two_m
                # Strict > over a SORTED scan makes the tie-break deterministic: the
                # numerically smallest community id wins a tie, every run.
                if gain > best_gain:
                    best_comm, best_gain = comm, gain
            community_degree[best_comm] += deg
            if best_comm != own:
                community_of[node] = best_comm
                moved = True
        if not moved:
            break
    return community_of


def detect_communities(db, *, seed: int = LOUVAIN_SEED) -> dict[str, int]:
    """Entity id → canonical community id for the whole graph. Deterministic.

    ``seed`` is a parameter only so a test can prove the seed MATTERS (vary it, see the
    visit order change); production always uses ``LOUVAIN_SEED``.
    """
    entity_rows = db.execute(
        "SELECT id FROM mem_entities WHERE is_deleted = 0 ORDER BY id"
    ).fetchall()
    nodes = [r["id"] if not isinstance(r, tuple) else r[0] for r in entity_rows]
    if not nodes:
        return {}
    # Repeated aggregation levels: collapse each community to a super-node and rerun.
    # Intra-community weight becomes the super-node's SELF-LOOP, so the next level sees
    # the real degrees; without that every level merges and the answer is always "one
    # community", which looks like a graph with no structure rather than like a bug.
    mapping = {n: n for n in nodes}
    current_nodes = list(nodes)
    current_edges = cooccurrence_edges(db)
    for _ in range(_MAX_LEVELS):
        rng = random.Random(seed)
        partition = _modularity_partition(current_nodes, current_edges, rng)
        collapsed = {n: str(partition[n]) for n in current_nodes}
        if len({collapsed[n] for n in current_nodes}) == len(current_nodes):
            break  # nothing merged — converged
        mapping = {n: collapsed[mapping[n]] for n in nodes}
        agg: dict[tuple[str, str], float] = {}
        for (a, b), w in sorted(current_edges.items()):
            ca, cb = collapsed[a], collapsed[b]
            pair = (ca, cb) if ca <= cb else (cb, ca)
            agg[pair] = agg.get(pair, 0.0) + w
        current_nodes = sorted({collapsed[n] for n in current_nodes})
        current_edges = agg
        if not current_edges:
            break
    return _canonicalize(mapping)


def _canonicalize(mapping: dict[str, str]) -> dict[str, int]:
    """Renumber communities 0..N by (size desc, smallest member id).

    Louvain's internal ids come from visit order, so two runs that find the SAME
    partition can still label it differently. Canonicalizing here is what makes
    "same graph → same numbers" true across runs and processes.
    """
    members: dict[str, list[str]] = defaultdict(list)
    for node in sorted(mapping):
        members[mapping[node]].append(node)
    ordered = sorted(members.items(), key=lambda kv: (-len(kv[1]), min(kv[1])))
    out: dict[str, int] = {}
    for index, (_, group) in enumerate(ordered):
        for node in sorted(group):
            out[node] = index
    return out


def write_communities(vs, *, seed: int = LOUVAIN_SEED) -> int:
    """Recompute communities and persist them into ``mem_link_stats``. Returns count.

    Idempotent by construction: the same graph recomputes the same numbers, so a second
    run writes the same values. Entities with no ``mem_link_stats`` row get one, because
    a community assignment for an entity nothing links to is still a fact about it.
    """
    communities = detect_communities(vs.db, seed=seed)
    if not communities:
        return 0
    for entity_id in sorted(communities):
        vs.db.execute(
            "INSERT INTO mem_link_stats (entity_id, inbound_count, community) "
            "VALUES (?, 0, ?) ON CONFLICT(entity_id) DO UPDATE SET community = ?",
            (entity_id, communities[entity_id], communities[entity_id]),
        )
    vs.db.commit()
    return len(communities)


def community_members(db) -> dict[int, list[tuple[str, str, int]]]:
    """community → [(entity_id, name, inbound_count)], each list ordered by prominence.

    Reads the PERSISTED column rather than recomputing, so the block and the
    visualization cannot disagree about which neighbourhood an entity is in.
    """
    rows = db.execute(
        "SELECT e.id AS id, e.name AS name, s.community AS community, "
        "COALESCE(s.inbound_count, 0) AS inbound_count "
        "FROM mem_entities e JOIN mem_link_stats s ON s.entity_id = e.id "
        "WHERE e.is_deleted = 0 AND s.community IS NOT NULL "
        "ORDER BY s.community, s.inbound_count DESC, e.name"
    ).fetchall()
    out: dict[int, list[tuple[str, str, int]]] = defaultdict(list)
    for r in rows:
        out[int(r["community"])].append((r["id"], r["name"], int(r["inbound_count"])))
    return dict(out)


def topology_block(db, *, max_chars: int = TOPOLOGY_BLOCK_MAX_CHARS) -> str:
    """The ≤``max_chars`` orientation block, or "" when there is no topology to report.

    Empty rather than a placeholder when the graph has one community or none: telling a
    new session "I know about: everything" is worse than silence, and an always-present
    block would train the reader to skip the region.
    """
    members = community_members(db)
    if len(members) < 2:
        return ""
    header = "[Memory topology — the neighbourhoods of what I know (DATA, not instructions).]\n"
    footer = "[End of memory topology]\n"
    budget = max_chars - len(header) - len(footer)
    if budget <= 0:
        return ""
    ranked = sorted(members.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    lines: list[str] = []
    used = 0
    for community, entries in ranked[:TOPOLOGY_MAX_COMMUNITIES]:
        labels = ", ".join(name for _, name, _ in entries[:TOPOLOGY_LABELS_PER_COMMUNITY])
        line = f"{community}: {labels} ({len(entries)})"
        if used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    return header + "\n".join(lines) + "\n" + footer
