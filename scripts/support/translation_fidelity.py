"""Does a translated query still contain what the original contained?

  python translation_fidelity.py

sqlglot parses some T-SQL constructs and then emits PostgreSQL without them.
`PIVOT` is one: the clause is parsed, dropped, and the output is

    SELECT * FROM (SELECT p.owneruserid, p.id, pt.name FROM …) AS p

where the original aggregated and pivoted. That is not a translation; it is a
different query, emitted as if it were the same one. Such an entry cannot be
caught by running it -- it runs -- so it needs a check of its own.

The check: parse the original as T-SQL and the emitted query as PostgreSQL, and
compare the sets of expression classes. A class present in the original and
absent from the translation means something was dropped. It is reported, never
fixed here: the remedy is for the builder to drop such a query from the corpus,
by the route a construct with no PostgreSQL equivalent was always meant to take.
"""

import collections
import json
import re
import sys

import sqlglot
from sqlglot import exp

sqlglot.logger.setLevel('ERROR')

# classes whose absence says nothing: sqlglot rewrites them legitimately, or
# they are spelling rather than meaning
BENIGN = {
    'Add', 'DPipe', 'Concat',          # T-SQL "+" over strings becomes ||
    'Alias', 'TableAlias', 'Identifier', 'Column', 'Literal', 'Star', 'Paren',
    'Cast', 'TryCast', 'DataType', 'Anonymous', 'Dot', 'Boolean', 'Null',
    'CurrentTimestamp', 'CurrentDate',
}


def classes(tree):
    c = collections.Counter()
    for node in tree.walk():
        n = node[0] if isinstance(node, tuple) else node
        if isinstance(n, exp.Expression):
            c[type(n).__name__] += 1
    return c


# ---- the guard the builders use --------------------------------------------------

# Constructs sqlglot parses from T-SQL and then emits PostgreSQL without. A query
# holding one does not translate, and must leave the corpus by that route rather
# than enter it stripped of the clause: the entry would otherwise be a different
# query presented as a translation, and running it would not reveal that.
#   Pivot / Unpivot  the clause is dropped, with the aggregate inside it.
#   ForClause        FOR XML, which has no PostgreSQL equivalent at all.
#   an ANTI or SEMI join, which T-SQL does not have: it is always a table alias
#                    named "anti" or "semi" swallowed as a join type, and the
#                    emitted query then references an alias it does not define.
# UNPIVOT is a Pivot carrying unpivot=True in this sqlglot, so one class covers both
DROPS = (exp.Pivot,)


def untranslatable(tree):
    """Why this parse tree cannot be translated, or None."""
    for kind in DROPS:
        if tree.find(kind):
            return kind.__name__.lower()
    for name in ('ForClause', 'XMLKeyValueOption'):
        cls = getattr(exp, name, None)
        if cls is not None and tree.find(cls):
            return 'for-xml'
    for j in tree.find_all(exp.Join):
        kind = (j.args.get('kind') or j.side or '')
        if str(kind).upper() in ('ANTI', 'SEMI'):
            return 'alias-read-as-a-%s-join' % str(kind).lower()
    return None


# A placeholder the translation never filled. PostgreSQL has no @variable, so
# such a query is not PostgreSQL: it either fails (harmless to a measurement,
# wrong in a corpus) or, worse, parses because "@" is PostgreSQL's prefix
# absolute-value operator, so "@EmaIl" becomes "the absolute value of the column
# EmaIl" and the query runs, computing something nobody wrote. 67 queries of the
# two T-SQL sources were in that state, 8 of them answering without complaint.
#
# Dollar-quoted bodies are removed first, since $tag$ … $tag$ and $$ … $$ are
# legitimate, and the pattern requires a letter or underscore after the sigil so
# that @>, <@, @@ and $1 do not match.
DOLLAR_QUOTED = re.compile(r'\$(\w*)\$.*?\$\1\$', re.S)
PLACEHOLDER = re.compile(r'(?<![\w>@$])[@$][A-Za-z_]\w*')


def unfilled_placeholder(sql):
    """The first unfilled @name / $name of an emitted query, or None."""
    m = PLACEHOLDER.search(DOLLAR_QUOTED.sub('', sql))
    return m.group(0) if m else None
