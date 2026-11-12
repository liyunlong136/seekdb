"""Verify the filter-canonicalization logic used by the performance fix.

The rewrite folds a strictly ascending IN list over an integer column into
the equivalent range.  This script re-implements the same predicate in Python
and checks that the folded form selects exactly the same rows as the original
list, over a range of column domains and list shapes.
"""
import itertools
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MAX_IN_LIST_FOLD_SIZE = 1024


def is_ascending_integer_list(values):
    """Mirror of ObFilterCanonicalizer::is_ascending_integer_list()."""
    previous = None
    for value in values:
        if not isinstance(value, int):
            return False
        if previous is not None and value != previous + 1:
            return False
        previous = value
    return True


def fold(values):
    """Return (folded?, lower, upper) mirroring fold_in_list_to_range()."""
    if len(values) < 2 or len(values) > MAX_IN_LIST_FOLD_SIZE:
        return (False, None, None)
    if not is_ascending_integer_list(values):
        return (False, None, None)
    return (True, values[0], values[-1])


def rows_for_list(values, domain):
    return {v for v in domain if v in values}


def rows_for_range(lower, upper, domain):
    return {v for v in domain if lower <= v <= upper}


print("== 1. a folded list selects exactly the same rows as the list ==")

domains = [
    range(0, 40),
    range(-20, 20),
    range(1, 5),
    range(0, 1000, 7),
]

list_shapes = [
    [0, 1, 2],
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [5, 6, 7],
    [-3, -2, -1, 0, 1],
    list(range(0, 300)),
    [1],
    [],
    [2, 2, 3],
    [3, 2, 1],
    [0, 5, 10, 15, 20],
    [7],
    list(range(0, 50, 2)),
]

checked = 0
folded_count = 0
for domain, values in itertools.product(domains, list_shapes):
    folded, lower, upper = fold(values)
    if folded:
        folded_count += 1
        expected = rows_for_list(values, domain)
        actual = rows_for_range(lower, upper, domain)
        if expected != actual:
            raise SystemExit(
                "row set mismatch for list %r on domain %r: %r != %r"
                % (values[:10], list(domain)[:5], sorted(expected), sorted(actual)))
    checked += 1

print("   checked %d (list, domain) combinations" % checked)
print("   %d of them were folded" % folded_count)

print()
print("== 2. a list that is not folded is left alone ==")
for values, why in (
    ([1], "a single value is already an equality"),
    ([2, 2, 3], "the list has a duplicate"),
    ([3, 2, 1], "the list is not ascending"),
    ([0, 1, 2, 1, 3], "the list is not ascending"),
    ([0, 5, 10], "the list skips values"),
    ([0, 1, 2, 4, 5], "the list skips a value in the middle"),
):
    folded, _lower, _upper = fold(values)
    if folded:
        raise SystemExit("list %r must not be folded (%s)" % (values, why))
    print("   [OK] %-22r not folded (%s)" % (values, why))

print()
print("== 3. a list that is too long is not folded ==")
long_list = list(range(MAX_IN_LIST_FOLD_SIZE + 1))
folded, _lower, _upper = fold(long_list)
if folded:
    raise SystemExit("an over-long list must not be folded")
print("   [OK] %d values are not folded" % len(long_list))

print()
print("ALL CHECKS PASSED")
