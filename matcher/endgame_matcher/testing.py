from collections.abc import Collection


def set_mistmatch(left: Collection, right: Collection) -> tuple[set[str], set[str]]:
    """
    Returns a tuple of extras in left, extras in right
    """
    left = set(left)
    right = set(right)
    return left.difference(right), right.difference(left)
