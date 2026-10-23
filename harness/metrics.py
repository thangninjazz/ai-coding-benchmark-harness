"""Evaluation metrics: unbiased pass@k (Chen et al., 2021) and resolve rate."""

from math import comb
from typing import Dict, Iterable, Sequence


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased estimator of pass@k from ``n`` samples of which ``c`` are correct.

    pass@k = 1 - C(n - c, k) / C(n, k)
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= c <= n:
        raise ValueError("c must satisfy 0 <= c <= n")
    if not 1 <= k <= n:
        raise ValueError("k must satisfy 1 <= k <= n")
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def aggregate_pass_at_k(
    per_task: Dict[str, Sequence[bool]], ks: Iterable[int] = (1,)
) -> Dict[int, float]:
    """Mean pass@k over tasks. ``per_task`` maps task id -> per-sample correctness."""
    ks = list(ks)
    if not per_task:
        raise ValueError("no tasks to aggregate")
    out: Dict[int, float] = {}
    for k in ks:
        scores = [pass_at_k(len(s), sum(s), k) for s in per_task.values() if len(s) >= k]
        if not scores:
            raise ValueError(f"no task has at least k={k} samples")
        out[k] = sum(scores) / len(scores)
    return out
