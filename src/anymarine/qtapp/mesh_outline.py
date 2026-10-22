"""Outline groups for body meshes: triangles of one flat face share a group id.

ANY3dView draws outlines only at group boundaries, so a box shows its twelve
real edges and no per-triangle diagonals across the quad faces.
"""

from __future__ import annotations

import numpy as np


def _components(count: int, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Connected-component labels of an undirected graph on ``count`` nodes."""
    try:
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
    except ImportError:  # pure numpy fallback: min-label propagation
        label = np.arange(count)
        while a.size:
            low = np.minimum(label[a], label[b])
            new = label.copy()
            np.minimum.at(new, a, low)
            np.minimum.at(new, b, low)
            new = new[new]
            if np.array_equal(new, label):
                break
            label = new
        return label
    graph = coo_matrix((np.ones(a.size, dtype=np.int8), (a, b)), shape=(count, count))
    return connected_components(graph, directed=False)[1]


def coplanar_outline_groups(positions, triangles, angle_deg: float = 1.0) -> np.ndarray:
    """One uint32 id per triangle; triangles joined across a shared edge whose
    face normals differ by less than ``angle_deg`` get the same id.

    Ids are numbered in order of first appearance, so the result is
    deterministic. Edges shared by other than two triangles (open or
    non-manifold) never join; degenerate triangles keep a group of their own.
    Coincident vertices (unwelded meshes) are welded for adjacency only.
    """
    pos = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
    tri = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    count = len(tri)
    if count == 0:
        return np.zeros(0, dtype=np.uint32)
    scale = float(np.ptp(pos, axis=0).max()) if len(pos) else 0.0
    tol = max(scale, 1e-300) * 1e-9
    _, weld = np.unique(np.round(pos / tol).astype(np.int64), axis=0, return_inverse=True)
    weld = np.asarray(weld).reshape(-1)
    w = weld[tri]
    p0, p1, p2 = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    normal = np.cross(p1 - p0, p2 - p0)
    length = np.linalg.norm(normal, axis=1)
    longest = np.maximum.reduce([np.linalg.norm(p1 - p0, axis=1), np.linalg.norm(p2 - p1, axis=1),
                                 np.linalg.norm(p0 - p2, axis=1)])
    valid = (length > 1e-12 * np.maximum(longest, 1e-300) ** 2) & (length > 0)
    valid &= (w[:, 0] != w[:, 1]) & (w[:, 1] != w[:, 2]) & (w[:, 0] != w[:, 2])
    unit = np.zeros_like(normal)
    unit[valid] = normal[valid] / length[valid, None]

    ea = np.concatenate((w[:, 0], w[:, 1], w[:, 2]))
    eb = np.concatenate((w[:, 1], w[:, 2], w[:, 0]))
    owner = np.tile(np.arange(count), 3)
    lo, hi = np.minimum(ea, eb), np.maximum(ea, eb)
    key = lo * (int(weld.max()) + 1) + hi
    order = np.argsort(key, kind="stable")
    key, owner = key[order], owner[order]
    same = key[1:] == key[:-1]
    # edges used by exactly two triangle sides
    run_start = np.concatenate(([True], ~same))
    run_id = np.cumsum(run_start) - 1
    run_len = np.bincount(run_id)
    pair = np.flatnonzero(same & (run_len[run_id[1:]] == 2))
    ta, tb = owner[pair], owner[pair + 1]
    keep = valid[ta] & valid[tb] & (ta != tb)
    ta, tb = ta[keep], tb[keep]
    cosine = np.einsum("ij,ij->i", unit[ta], unit[tb])
    join = cosine >= np.cos(np.radians(float(angle_deg)))
    label = _components(count, ta[join], tb[join])
    _, first, inverse = np.unique(label, return_index=True, return_inverse=True)
    rank = np.empty(len(first), dtype=np.int64)
    rank[np.argsort(first, kind="stable")] = np.arange(len(first))
    return rank[np.asarray(inverse).reshape(-1)].astype(np.uint32)
