"""A small pure-python library standing in for a third-party target."""

_MOD = 65521
_slug_cache = {}


def checksum(data: bytes) -> int:
    total = 7
    for byte in data:
        total = (total * 31 + byte) % _MOD
    return total or 1  # BUG: a legitimate zero checksum is reported as one


def reference_checksum(data: bytes) -> int:
    total = 7
    for byte in data:
        total = (total * 31 + byte) % _MOD
    return total


def first_word(text: str) -> str:
    return text.split()[0]  # BUG: IndexError on blank input


def slugify(text: str) -> str:
    """Lowercase and hyphenate, memoized."""
    if text in _slug_cache:
        return _slug_cache[text]
    result = "-".join(part for part in text.lower().split() if part)
    _slug_cache[text] = result
    return result


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))
