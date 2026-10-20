"""The constants a query compares each column with.

Shared by the generated corpora (TPC-H, TPC-DS). An instance whose rows satisfy
none of a query's predicates makes the query answer nothing, and an empty answer
tests far less than a non-empty one: TPC-DS query 90 divided by zero because its
`time_dim` held hours 1, 4, 6 and 12 while the query counts the 14..15 window,
and 56 of its 87 comparable queries answered nothing at all.

Dealing a GLOBAL pool of the query's literals across the rows is not enough --
each column then covers an arbitrary window of the pool. The constants have to be
attached to the column that is compared with them.
"""

import re

BETWEEN = re.compile(r'(?i)(?:\w+\.)?(\w+)\s+between\s+([\d+\s]+?)\s+and\s+([\d+\s]+?)(?=\s|\)|$)')
EQ = re.compile(r'(?i)(?:\w+\.)?(\w+)\s*=\s*(\d+)\b')
IN = re.compile(r'(?i)(?:\w+\.)?(\w+)\s+in\s*\(\s*([\d,\s]+?)\)')


def _arith(text):
    """`12`, or `12+1` as the TPC kits write a window's upper bound."""
    parts = [int(x) for x in re.findall(r'\d+', text)]
    return sum(parts) if parts else None


def column_constants(sql):
    """col -> the set of integers the query compares it with, bounds included.

    A wide range contributes representatives rather than every value, so
    `wp_char_count between 5000 and 5200` costs four values and not 201.
    `_sk`/`key` columns are left out of the equality case: those are join keys,
    generated in a shared small range so the joins meet.
    """
    out = {}
    for m in BETWEEN.finditer(sql):
        lo, hi = _arith(m.group(2)), _arith(m.group(3))
        if lo is None or hi is None or hi < lo:
            continue
        vals = range(lo, hi + 1) if hi - lo <= 8 else (lo, lo + 1, (lo + hi) // 2, hi)
        out.setdefault(m.group(1).lower(), set()).update(vals)
    for m in EQ.finditer(sql):
        col = m.group(1).lower()
        if not (col.endswith('_sk') or col.endswith('key')):
            out.setdefault(col, set()).add(int(m.group(2)))
    for m in IN.finditer(sql):
        col = m.group(1).lower()
        if col.endswith('_sk') or col.endswith('key'):
            continue
        for v in re.findall(r'\d+', m.group(2)):
            out.setdefault(col, set()).add(int(v))
    return out


def deal(own, pool, col, row):
    """A value for `col` in row `row`: the column's own constants first.

    Each of a column's constants appears in some row, then the general pool
    continues from a per-column offset (stable, not hash(), which is salted).
    """
    own = sorted(own or ())
    if own and row < len(own):
        return own[row]
    pool = own + list(pool)
    if not pool:
        return 1
    return pool[(row + sum(col.encode())) % len(pool)]
