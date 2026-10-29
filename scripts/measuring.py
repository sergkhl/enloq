"""Small statistics for the finite checks' reports: exact binomial ranges,
confusion counts and percentiles, with no dependency beyond the standard
library."""

import math


def _at_most(k, n, p):
    """P(X <= k) for X ~ Binomial(n, p)."""
    if k < 0:
        return 0.0
    if k >= n or p <= 0:
        return 1.0
    if p >= 1:
        return 0.0
    lp, lq = math.log(p), math.log1p(-p)
    total = sum(
        math.exp(
            math.lgamma(n + 1)
            - math.lgamma(i + 1)
            - math.lgamma(n - i + 1)
            + i * lp
            + (n - i) * lq
        )
        for i in range(k + 1)
    )
    return min(1.0, total)


def _root(increasing):
    low, high = 0.0, 1.0
    for _ in range(200):
        middle = (low + high) / 2
        if increasing(middle) < 0:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def clopper_pearson(k, n, confidence=0.95):
    """The two-sided exact (Clopper-Pearson) range for k of n, as fractions."""
    if not 0 <= k <= n or n == 0:
        return None
    tail = (1 - confidence) / 2
    low = 0.0 if k == 0 else _root(lambda p: (1 - _at_most(k - 1, n, p)) - tail)
    high = 1.0 if k == n else _root(lambda p: tail - _at_most(k, n, p))
    return low, high


def rate(k, n):
    """A count with its share and 95% exact range, rounded for a report."""
    bounds = clopper_pearson(k, n)
    return {
        "count": k,
        "of": n,
        "rate": round(k / n, 4) if n else None,
        "range_95": [round(bounds[0], 4), round(bounds[1], 4)] if bounds else None,
    }


def paired(first, second):
    """Two ways of answering the same cases, each case right or wrong: how
    often both, either alone or neither was right, with the exact two-sided
    McNemar (sign test) p-value of the cases they disagree on."""
    pairs = list(zip(first, second, strict=True))
    only_first = sum(1 for a, b in pairs if a and not b)
    only_second = sum(1 for a, b in pairs if b and not a)
    differing = only_first + only_second
    return {
        "both": sum(1 for a, b in pairs if a and b),
        "only_first": only_first,
        "only_second": only_second,
        "neither": sum(1 for a, b in pairs if not a and not b),
        "p_value": round(
            min(1.0, 2 * _at_most(min(only_first, only_second), differing, 0.5)), 4
        )
        if differing
        else 1.0,
    }


def percentile(values, share):
    """Linear interpolation between closest ranks; None for no values."""
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * share
    below = math.floor(position)
    above = min(below + 1, len(values) - 1)
    return values[below] + (values[above] - values[below]) * (position - below)


def confusion(pairs):
    """Counts for (labelled, predicted) booleans: supported is positive."""
    pairs = list(pairs)
    return {
        "supported_kept": sum(1 for label, kept in pairs if label and kept),
        "supported_removed": sum(1 for label, kept in pairs if label and not kept),
        "unsupported_kept": sum(1 for label, kept in pairs if not label and kept),
        "unsupported_removed": sum(
            1 for label, kept in pairs if not label and not kept
        ),
    }


def error_rates(pairs):
    """The two ways a grader is wrong, each with its exact 95% range."""
    counts = confusion(pairs)
    supported = counts["supported_kept"] + counts["supported_removed"]
    unsupported = counts["unsupported_kept"] + counts["unsupported_removed"]
    return {
        **counts,
        "supported_removed_rate": rate(counts["supported_removed"], supported),
        "unsupported_kept_rate": rate(counts["unsupported_kept"], unsupported),
    }
