"""T-SQL constructs sqlglot passes through unchanged, rendered for PostgreSQL.

Used by the SQLShare and SEDE builders, which both translate T-SQL with sqlglot and
both inherit the same gaps: sqlglot emits `PATINDEX`, `DATE_FROM_PARTS`, `STR` and
`NUMBER_TO_STR` verbatim, and PostgreSQL has none of them, so without a rendering
the query fails.

Each rule is exact unless its note says otherwise: three are not, `str`, `round(x, n)`
and `number_to_str`, and their notes say how they differ. A rule that would change
which rows a query selects is not here. `ISNUMERIC` is the case in point: a regex
test is close but not equal -- it rejects `1,000` and `$5`, which T-SQL accepts --
so rendering it would make a query select different rows than its author saw, and
it is left alone; such a query fails on PostgreSQL and is excluded. `GETUTCDATE`
is left alone as well, and a query using it is excluded the same way.
"""
import re

# PATINDEX('%X%', e) is the position of X in e, 0 if absent, which is exactly
# POSITION(X IN e) -- but only when the pattern's wildcards are the two outer ones.
# 371 of the 408 occurrences in the two corpora are that shape; a pattern with an
# internal `%`, a `_`, or a T-SQL character class ('[0-9]%') is left alone rather than
# approximated, since PATINDEX then means something POSITION cannot express.
PATINDEX_SIMPLE = re.compile(
    r"(?i)\bpatindex\s*\(\s*N?'%([^%_'\[\]]+)%'\s*,\s*", )

# `round(x, n)`: the argument is cast to NUMERIC unless it is already a cast to
# NUMERIC. A cast to anything else, as in `ROUND(CAST("lat" AS REAL), 2)`, still
# needs it.
ROUND_TWO_ARG = re.compile(
    r'(?i)\bround\s*\(\s*((?:[^(),]|\([^()]*\))+?)\s*,\s*(\d+)\s*\)')


def _round_to_numeric(m):
    arg, digits = m.group(1), m.group(2)
    if re.search(r'(?i)AS\s+numeric\s*\)\s*$', arg):
        return m.group(0)
    return 'round(CAST(%s AS numeric), %s)' % (arg, digits)


# PostgreSQL's make_timestamp takes (int, int, int, int, int, double precision) and the
# corpus calls it with bigints, which matches no overload. Casting each argument is exact
# for the values involved -- a year, a month, a day, an hour, a minute and seconds.
MAKE_TIMESTAMP = re.compile(
    r'(?i)\bmake_timestamp\s*\(\s*([^(),]+?)\s*,\s*([^(),]+?)\s*,\s*([^(),]+?)\s*,'
    r'\s*([^(),]+?)\s*,\s*([^(),]+?)\s*,\s*([^(),]+?)\s*\)')


def _make_timestamp_casts(m):
    a = [m.group(i) for i in range(1, 7)]
    if any('CAST' in x.upper() for x in a):
        return m.group(0)
    return ('make_timestamp(CAST(%s AS int), CAST(%s AS int), CAST(%s AS int), '
            'CAST(%s AS int), CAST(%s AS int), CAST(%s AS double precision))' % tuple(a))


# T-SQL counts datetimes from 1900-01-01, so `DATEADD(day, n, 0)` means "n days after
# that date" and sqlglot renders it as `0 + INTERVAL '1 DAY' * n`, which PostgreSQL
# rejects: there is no integer + interval. Naming the epoch is exactly what T-SQL means.
# 88 SEDE queries fail on this one shape.
TSQL_EPOCH = re.compile(r"(?i)(?<![\w.])0\s*\+\s*INTERVAL\s*'")

RULES = [
    (TSQL_EPOCH, "CAST('1900-01-01' AS timestamp) + INTERVAL '",
     "`0 + INTERVAL '…'` is T-SQL's datetime zero, which is 1900-01-01: PostgreSQL has "
     "no integer + interval, and the epoch is what the 0 means"),
    (MAKE_TIMESTAMP, '_make_timestamp_casts',
     'make_timestamp casts its arguments: the corpus calls it with bigints and '
     'PostgreSQL has only the (int, int, int, int, int, double precision) form'),
    # PostgreSQL has round(numeric, integer) and not round(double precision, integer).
    # A view using it fails to create, and every query reading the view then fails
    # with a missing relation.
    (ROUND_TWO_ARG, '_round_to_numeric',
     'round(x, n) casts its argument to numeric, PostgreSQL having no '
     'round(double precision, integer)'),
    # T-SQL's VAR and VARP are PostgreSQL's variance (var_samp) and var_pop
    (re.compile(r'(?i)\bvar\s*\('), 'variance(',
     "T-SQL's VAR(x) is variance(x)"),
    (re.compile(r'(?i)\bvarp\s*\('), 'var_pop(',
     "T-SQL's VARP(x) is var_pop(x)"),
    # T-SQL overloads `+` for string concatenation; PostgreSQL has `||` and refuses
    # `unknown + character varying`. Only an operand that is a STRING LITERAL is rewritten: there `+` cannot be
    # arithmetic, so the rendering is exact. A `+` between two columns is left alone,
    # since without types it could be either and guessing would change what the query
    # computes.
    (re.compile(r"('(?:[^']|'')*')\s*\+(?!\+)"), r'\1 ||',
     "a `+` next to a string literal is T-SQL concatenation, rendered as `||`"),
    (re.compile(r"\+\s*('(?:[^']|'')*')"), r'|| \1',
     "a `+` next to a string literal is T-SQL concatenation, rendered as `||`"),
    (re.compile(r"(?i)\bdate_from_parts\s*\("), 'make_date(',
     'date_from_parts(y, m, d) is make_date(y, m, d)'),
    (re.compile(r'(?i)\bnumber_to_str\s*\(\s*(.+?)\s*,\s*(\d+)\s*\)'),
     r'CAST(round(CAST(\1 AS numeric), \2) AS text)',
     'number_to_str(x, d) becomes a rounded cast to text, as in the BEAVER builder: '
     'to_char would need a format string rather than a number of decimals'),
    (re.compile(r'(?i)\bstr\s*\(\s*([^(),]+?)\s*\)'),
     r"trim(to_char(round(CAST(\1 AS numeric)), 'FM999999999999'))",
     "str(x) rounds to an integer and returns text; T-SQL pads it to width 10 and this "
     "does not, so the value differs in whitespace from what the query's author saw"),
]


def render(sql):
    """(rendered sql, [notes]) -- the notes name every rule that fired."""
    notes = []

    def patindex(m):
        notes.append('patindex on a %X% pattern becomes position(X in e)')
        return "position('%s' in " % m.group(1).replace("'", "''")

    out = PATINDEX_SIMPLE.sub(patindex, sql)
    for pat, repl, note in RULES:
        new = pat.sub(globals()[repl] if isinstance(repl, str) and repl.startswith('_')
                      else repl, out)
        if new != out:
            notes.append(note)
            out = new
    return out, sorted(set(notes))
