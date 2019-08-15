from typing import Collection, Set, Tuple


def set_mistmatch(left: Collection, right: Collection) -> Tuple[Set[str], Set[str]]:
    """
    Returns a tuple of extras in left, extras in right
    """
    left = set(left)
    right = set(right)
    return left.difference(right), right.difference(left)
