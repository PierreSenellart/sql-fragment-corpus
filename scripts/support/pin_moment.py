"""Pin the moment a query reads, so that its answer is reproducible.

  python3 pin_moment.py < query.sql       # the query with the moment pinned

`current_timestamp`, `current_date`, `current_time`, `localtimestamp`,
`localtime`, `now()`, `transaction_timestamp()` and `statement_timestamp()` are
fixed for the whole statement, so an SQL answer is a function of the instance
and of the moment of execution. The corpus fixes the moment: every such read in
its queries and setups is replaced by 2026-06-15 12:00:00+02. This module makes
that replacement; the SQLShare script uses it on the text it builds.

Functions whose value changes WITHIN a statement -- `clock_timestamp()`,
`timeofday()`, `random()`, uuid generation -- are not pinned: a query using one
has no determined answer and is not in the corpus.

The rewrite splices text at the parse tree's locations, so everything it does
not touch is preserved byte for byte. A query that does not parse, or that
still holds such a node afterwards, is returned unchanged.
"""
import os
import re
import sys

from pglast import parse_sql
import pglast.visitors as V

# The moment the measurement used. Set MOMENT to another date to pin a
# different one; the corpus's recorded row counts are those of this one.
MOMENT_DATE = os.environ.get('MOMENT', '2026-06-15')
MOMENT_TZ = '%s 12:00:00+02' % MOMENT_DATE
MOMENT = '%s 12:00:00' % MOMENT_DATE
MOMENT_TIME_TZ = '12:00:00+02'
MOMENT_TIME = '12:00:00'

# the SQLValueFunction operations that are constants of the query, and the typed
# literal each becomes; the N forms carry a precision, which a fixed value ignores
SVF = {
    'SVFOP_CURRENT_DATE': "DATE '%s'" % MOMENT_DATE,
    'SVFOP_CURRENT_TIME': "TIMETZ '%s'" % MOMENT_TIME_TZ,
    'SVFOP_CURRENT_TIME_N': "TIMETZ '%s'" % MOMENT_TIME_TZ,
    'SVFOP_CURRENT_TIMESTAMP': "TIMESTAMPTZ '%s'" % MOMENT_TZ,
    'SVFOP_CURRENT_TIMESTAMP_N': "TIMESTAMPTZ '%s'" % MOMENT_TZ,
    'SVFOP_LOCALTIME': "TIME '%s'" % MOMENT_TIME,
    'SVFOP_LOCALTIME_N': "TIME '%s'" % MOMENT_TIME,
    'SVFOP_LOCALTIMESTAMP': "TIMESTAMP '%s'" % MOMENT,
    'SVFOP_LOCALTIMESTAMP_N': "TIMESTAMP '%s'" % MOMENT,
}
FUNCS = {'now': "TIMESTAMPTZ '%s'" % MOMENT_TZ,
         'transaction_timestamp': "TIMESTAMPTZ '%s'" % MOMENT_TZ,
         # fixed within one statement, so a constant of the query too
         'statement_timestamp': "TIMESTAMPTZ '%s'" % MOMENT_TZ}
# PostgreSQL's special date/time inputs ('now', 'today', 'tomorrow',
# 'yesterday') read the clock in the same way, and are NOT pinned here:
# `A_Const` carries no location in pglast 8.4, so they cannot be spliced from
# the parse tree, and splicing them by text would mean a regular expression
# over string literals, where the same words are ordinary data. No query of
# this corpus uses one.

KEYWORD = re.compile(r'(?i)\A(current_timestamp|current_date|current_time'
                     r'|localtimestamp|localtime)\s*(\(\s*\d+\s*\))?')
CALL = re.compile(r'(?i)\A(now|transaction_timestamp|statement_timestamp)\s*\(\s*\)')


def _edits(sql):
    """(start, end, replacement) for every constant of the query in `sql`."""
    out = []

    class Find(V.Visitor):
        def visit(self, ancestors, node):
            name = type(node).__name__
            loc = getattr(node, 'location', None)
            if loc is None or loc < 0:
                return
            if name == 'SQLValueFunction':
                text = SVF.get(str(node.op).split('.')[-1])
                if text:
                    m = KEYWORD.match(sql[loc:])
                    if m:
                        out.append((loc, loc + m.end(), text))
            elif name == 'FuncCall':
                fn = '.'.join(x.sval for x in node.funcname)
                if fn in FUNCS:
                    m = CALL.match(sql[loc:])
                    if m:
                        out.append((loc, loc + m.end(), FUNCS[fn]))

    Find()(parse_sql(sql))
    return sorted(out, reverse=True)


def pin(sql):
    """Rewrite `sql` with the moment pinned; unchanged if that cannot be done."""
    try:
        edits = _edits(sql)
    except Exception:
        return sql                      # does not parse: returned unchanged
    if not edits:
        return sql
    out = sql
    for start, end, text in edits:
        out = out[:start] + text + out[end:]
    try:                                # the result must parse, and be free of them
        if _edits(out):
            return sql
    except Exception:
        return sql
    return out


if __name__ == '__main__':
    print(pin(sys.stdin.read()), end='')
