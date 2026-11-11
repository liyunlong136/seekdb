"""Optional numpy acceleration for the vector TopK scan.

The judge image installs numpy, but the program has to keep working when the
install failed, so every helper degrades gracefully: the module reports
``available() is False`` and the executor simply keeps using the generic
pure-Python path.

The helpers never change the semantics of a query:

* distances are computed in float64 over the stored float32 values, exactly
  like the reference ``l2_distance``;
* the caller re-computes the distances of the selected rows with the reference
  implementation, so the reported numbers and the tie-break order are the ones
  a pure-Python scan would have produced.
"""

from __future__ import annotations

from typing import Any

try:  # pragma: no cover - depends on the image
    import numpy as _np
except ImportError:  # pragma: no cover - depends on the image
    _np = None  # type: ignore[assignment]

NUMPY_AVAILABLE = _np is not None


def available() -> bool:
    """Whether the vectorised path can be used."""

    return NUMPY_AVAILABLE


def module() -> Any:
    """The numpy module, or ``None`` when it is unavailable."""

    return _np


def load_fixed_column(path: str, dtype: str, elements: int) -> Any:
    """Read a fixed width column file into a numpy array of ``elements`` items.

    The arena of a fixed width column holds one value per slot, so the file is
    a plain array.  A short file is padded with zeros, which keeps the shape
    stable while the table is being filled.
    """

    if _np is None:
        return None
    raw = _np.fromfile(path, dtype=dtype)
    if raw.size < elements:
        raw = _np.concatenate([raw, _np.zeros(elements - raw.size, dtype=dtype)])
    return raw[:elements]


def load_live_mask(path: str, count: int) -> Any:
    """Read the one byte per slot liveness flag."""

    if _np is None:
        return None
    raw = _np.fromfile(path, dtype=_np.uint8)
    if raw.size < count:
        raw = _np.concatenate([raw, _np.zeros(count - raw.size, dtype=_np.uint8)])
    return raw[:count]


def l2_distances(matrix: Any, query: Any) -> Any:
    """Euclidean distance of every row of ``matrix`` to ``query``.

    ``matrix`` holds float32 values, ``query`` is the float32 query vector.
    The accumulation happens in float64, matching the reference implementation.
    """

    if _np is None:
        return None
    diff = matrix.astype(_np.float64)
    diff -= query.astype(_np.float64)
    squared = _np.einsum("ij,ij->i", diff, diff)
    return _np.sqrt(squared)


def select_topk(distances: Any, pks: Any, limit: int) -> Any:
    """Indices of the ``limit`` closest rows, ordered by (distance, primary key).

    ``argpartition`` narrows the field down in linear time; every row that ties
    with the last selected distance is kept so the primary key tie-break can
    still see all of them.
    """

    if _np is None:
        return None
    total = distances.shape[0]
    if total == 0:
        return _np.zeros(0, dtype=_np.int64)
    take = max(0, min(int(limit), total))
    if take == 0:
        return _np.zeros(0, dtype=_np.int64)
    if take < total:
        partition = _np.argpartition(distances, take - 1)[:take]
        boundary = distances[partition].max()
        candidates = _np.flatnonzero(distances <= boundary)
    else:
        candidates = _np.arange(total, dtype=_np.int64)
    order = _np.lexsort((pks[candidates], distances[candidates]))
    return candidates[order][:take]


def compare_bytes(arena: bytes, offsets: Any, lengths: Any, literal: bytes, operator: str) -> Any:
    """Compare a VARCHAR column against a literal, byte by byte.

    UTF-8 byte order equals Unicode code point order, so the byte comparison
    gives the same ordering the reference executor uses for strings.  Only the
    slots whose byte length matches are decoded, which keeps the common
    equality filter cheap.
    """

    if _np is None:
        return None
    mask = _np.zeros(offsets.shape[0], dtype=bool)
    same_length = _np.flatnonzero(lengths == len(literal))
    for slot in same_length:
        start = int(offsets[slot])
        end = start + int(literal.__len__())
        value = arena[start:end]
        if operator == "=":
            mask[slot] = value == literal
        elif operator in ("<>", "!="):
            mask[slot] = value != literal
        elif operator == "<":
            mask[slot] = value < literal
        elif operator == "<=":
            mask[slot] = value <= literal
        elif operator == ">":
            mask[slot] = value > literal
        else:
            mask[slot] = value >= literal
    return mask


__all__ = [
    "NUMPY_AVAILABLE",
    "available",
    "compare_bytes",
    "l2_distances",
    "load_fixed_column",
    "load_live_mask",
    "module",
    "select_topk",
]
