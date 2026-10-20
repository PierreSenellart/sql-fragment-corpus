"""Scenarios from the 22 TPC-H queries.

  python3 tpch_extract.py OUT.jsonl --kit DBGEN_DIR

The query text is the functional query definition of the specification
(TPC-H 3.0.1, clause 2.4), taken from the official `queries/` templates, with the
substitution parameters filled by the qualification values of each query's
"Query Validation" clause (2.4.N.4), as extracted from the specification into
`values.json` and `slotmap.json`. Beyond that, two queries differ from the
template:

  * Q1: `interval '[DELTA]' day (3)` loses its precision `(3)`, which PostgreSQL
    does not accept. Over a whole number of days the precision changes nothing.
    Recorded in the entry's `dialect` field.
  * Q15: the view is named `revenue0`, as clause 2.1.2.3 requires for stream 0;
    the view goes in the scenario's setup, the SELECT is the query, and the
    closing `DROP VIEW` is left out. Recorded in `deviation`.

Nothing else is changed: no unnesting, no rewriting of Q15's view into a CTE, no
precomputing of date arithmetic, no removal of aggregates. An entry's `original`
keeps the template with its values filled and neither change applied, and
`verify` checks every entry against it.

The data is generated rather than taken at a scale factor: the corpus needs a
small instance a query can be checked against. Values are drawn from each
query's own literals so its predicates select something, and every column whose
name ends in "key" is drawn from a small shared range so that joins match.
"""

import json
import os
import random
import re
import sys

import collections

# the helper modules ship beside this script, in support/
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'support'))

import declared_keys

# set from the command line: see main(). HERE is where this script lives, which
# is where the substitution values ship.
HERE = os.path.dirname(os.path.abspath(__file__))
KIT = None

TYPES = {'INTEGER': 'integer', 'DECIMAL': 'numeric(15,2)', 'CHAR': 'char',
         'VARCHAR': 'varchar', 'DATE': 'date'}

# qgen directives in the templates: the stream separator, the row-count limit
# and the comment header. They are not part of the query text.
DIRECTIVE = re.compile(r'^\s*:[xon](\s+-?\d+)?\s*$', re.M)


def schema():
    """The 8 TPC-H tables, from the specification's own DDL."""
    tables = {}
    text = open(os.path.join(KIT, 'dss.ddl')).read()
    for m in re.finditer(r'CREATE TABLE\s+(\w+)\s*\((.*?)\);', text, re.S | re.I):
        cols = []
        for part in m.group(2).split(','):
            part = part.strip()
            if not part or part.upper().startswith(('PRIMARY', 'FOREIGN', 'CONSTRAINT')):
                continue
            bits = part.split()
            if len(bits) < 2:
                continue
            name, ty = bits[0].lower(), bits[1].upper()
            base = re.match(r'([A-Z]+)(\((\d+)(,\s*\d+)?\))?', ty)
            kind = TYPES.get(base.group(1))
            if kind is None:
                continue
            if kind in ('char', 'varchar'):
                kind = '%s(%s)' % (kind, base.group(3) or '1')
            cols.append((name, kind))
        tables[m.group(1).lower()] = cols
    return tables


def query(n, values, slots, render=True):
    """Query n of the specification, with its substitution parameters filled.

    With `render`, the dialect rendering of RENDERED is applied too; without it
    the text is the template's, which is what an entry's `original` keeps.

    The template's :1, :2 … are NOT in the order the specification numbers the
    parameters: in query 19, :1 is BRAND1 while the specification's first
    parameter is QUANTITY1, so substituting by position puts a quantity where a
    brand belongs and yields a query that is not query 19. The slots are
    therefore mapped to names by aligning the placeholders of the template with
    the [NAME] placeholders of the specification's own functional query
    definition, in text order, and the value is then looked up by name.
    """
    text = open(os.path.join(KIT, 'queries', '%d.sql' % n)).read()
    text = re.sub(r'^\s*--.*$', '', text, flags=re.M)      # the -- $ID$ header
    text = DIRECTIVE.sub('', text)
    # longest slot first, so that :1 does not eat the :1 of :12
    for slot in sorted(slots, key=lambda s: -len(s)):
        text = text.replace(':' + slot, values[slots[slot]])
    # :s is the stream identifier, "replaced by a unique stream identification
    # number (starting with 0)" by clause 2.1.2.3
    text = text.replace(':s', '0')
    for pat, repl, _ in (RENDERED.get(n, ()) if render else ()):
        text, k = re.subn(pat, repl, text)
        assert k, 'the dialect rendering of query %d matched nothing' % n
    left = re.findall(r':\w+', text)
    if left:
        raise ValueError('query %d still holds %s' % (n, left))
    # the functional definitions are a statement sequence for Q15 (create view,
    # select, drop view) and a single statement otherwise
    stmts = [s.strip() for s in text.split(';') if s.strip()]
    return stmts


def tables_of(stmts, known):
    """Which of the 8 tables the statements read, by name."""
    text = ' '.join(stmts).lower()
    return sorted(t for t in known if re.search(r'\b%s\b' % re.escape(t), text))


import predicates


def literals(stmts):
    lits = {'s': set(), 'n': set(), 'd': set()}
    text = ' '.join(stmts)
    for m in re.finditer(r"'([^']*)'", text):
        v = m.group(1)
        if re.match(r'^\d{4}-\d{2}-\d{2}$', v):
            lits['d'].add(v)
        elif v.strip('%'):
            lits['s'].add(v.strip('%').strip())
    for m in re.finditer(r'(?<![\w.\'])(\d+(?:\.\d+)?)(?![\w.\'])', text):
        try:
            lits['n'].add(float(m.group(1)))
        except ValueError:
            pass
    return lits


def value(col, ty, i, rows, rng, lits, colvals=None):
    colvals = colvals or {}
    if col.endswith('key'):
        # a shared small range, so that joins match
        return str(rng.randint(1, max(2, rows)))
    if ty.startswith('date'):
        pool = sorted(lits['d']) or ['1995-03-15']
        base = rng.choice(pool)
        y, mo, d = (int(x) for x in base.split('-'))
        # a few days either side of a literal, so that a range predicate can
        # both hold and fail across the generated rows
        d = min(28, max(1, d + rng.choice((-20, -2, 0, 1, 15, 40) if rows > 2 else (0, 1))))
        mo = mo + (1 if d > 28 else 0)
        return "'%04d-%02d-%02d'" % (y, min(12, mo), min(28, d))
    if ty.startswith('numeric'):
        pool = [x for x in lits['n'] if 0 < x < 10000] or [10.0]
        v = rng.choice(sorted(pool))
        return '%.2f' % (v * rng.choice((0.5, 1.0, 1.5)) + rng.choice((0, 1)))
    if ty.startswith('integer'):
        # NOT the per-column dealing that TPC-DS uses. Measured: it took TPC-H from
        # 16 of 22 non-empty answers down to 11. `predicates.deal` puts a column's
        # own constants in, which satisfies BETWEEN and `=` -- what TPC-DS asks --
        # but not the strict inequalities TPC-H asks (`l_quantity < 24` is false of
        # 24), and it displaced the varied values the draw below produces. The
        # dates above already straddle each literal on purpose, which is the same
        # idea done right for ranges.
        pool = [int(x) for x in lits['n'] if 0 <= x < 200] or [1, 2, 3]
        return str(rng.choice(sorted(set(pool)) + [1, 2, 25]))
    # char / varchar: the query's own strings, so LIKE and IN select something
    words = sorted(lits['s']) or ['foo']
    v = rng.choice(words)
    if col == 'c_phone':                       # Q22 matches on the first two
        v = '%s-%03d-%03d-%04d' % (rng.choice(sorted(
            {w for w in words if re.match(r'^\d\d$', w)}) or ['13']),
            rng.randint(100, 999), rng.randint(100, 999), rng.randint(1000, 9999))
    elif col.endswith('comment') and rng.random() < 0.5:
        v = 'a %s and %s here' % (rng.choice(words), rng.choice(words))
    n = int(re.search(r'\((\d+)\)', ty).group(1))
    return "'%s'" % v.replace("'", "''")[:n]


def tokens(text):
    """The query as a token sequence, with every literal and placeholder blanked,
    so that a clause added, removed or reordered shows and a substituted value
    does not."""
    text = re.sub(r'^\s*--.*$', '', text, flags=re.M)
    text = DIRECTIVE.sub('', text)
    text = re.sub(r"'[^']*'", '@', text)
    text = re.sub(r':\w+', '@', text)
    text = re.sub(r'(?<![\w.])\d+(\.\d+)?', '@', text)
    return re.findall(r'@|[A-Za-z_]\w*|<=|>=|<>|[(),;*+\-/<>=.]', text.lower())


# The two differences allowed between a generated entry and the template, both
# of them this script's doing and neither a change to the query:
#   - the statement terminator, since the entry holds statements, not a script;
#   - in Q15, the view name carries the stream identifier (revenue0, as clause
#     2.1.2.3 requires) and the trailing DROP VIEW is left out, the caller
#     dropping the whole schema after the scenario.
# Dialect renderings: changes made only so that PostgreSQL accepts the query,
# which is what the other corpora do for T-SQL. Nothing else may be changed.
#
# Query 1 is the only one of the 22 that PostgreSQL cannot parse as written.
# "interval '[DELTA]' day (3)" is SQL-92's interval leading precision, and
# `SELECT date '1998-12-01' - interval '90' day (3)` is a syntax error in
# PostgreSQL 18. Dropping the precision is faithful: over a whole number of days
# a fractional-seconds precision of 3 changes nothing observable, so the
# rendered query means what the specification's means.
RENDERED = {1: [(r"day\s*\(\s*3\s*\)", 'day',
                 "interval leading precision dropped: \"day (3)\" to \"day\". "
                 "PostgreSQL has no grammar for it, and over a whole number of "
                 "days it changes nothing observable.")]}

KNOWN_DEVIATION = {15: 'the trailing DROP VIEW is omitted: the caller drops the '
                       'scenario schema whole. The view name carries the stream '
                       'identifier 0, as clause 2.1.2.3 requires.'}


def verify(path):
    """Every entry against the specification's template. Prints and returns the
    number that differ by anything other than the two allowed differences."""
    bad = 0
    for line in open(path):
        r = json.loads(line)
        n = int(r['id'].split('/')[1])
        # `original` is the template with its values filled and no rendering, so
        # it is compared with the template as it stands
        text = open(os.path.join(KIT, 'queries', '%d.sql' % n)).read()
        want = tokens(text)
        got = tokens(r['original'])
        if n in KNOWN_DEVIATION:
            # the template's "revenue:s" is two tokens, the entry's "revenue0"
            # one; and the template ends with a DROP VIEW the entry leaves out
            want = [t for k, t in enumerate(want)
                    if not (t == '@' and k and want[k - 1] == 'revenue')]
            # `original` keeps the whole statement sequence, DROP VIEW included;
            # it is the scenario's `queries` that leave the drop out
            got = ['revenue' if t == 'revenue0' else t for t in got]
        while want and want[-1] == ';':
            want.pop()
        while got and got[-1] == ';':
            got.pop()
        if want != got:
            bad += 1
            j = next((k for k in range(min(len(want), len(got))) if want[k] != got[k]),
                     min(len(want), len(got)))
            print('query %02d differs: template %s / entry %s'
                  % (n, want[max(0, j - 3):j + 3], got[max(0, j - 3):j + 3]), file=sys.stderr)
    print("%d of 22 entries are the specification's query text, "
          "differing only where a substitution parameter sits" % (22 - bad))
    return bad


def main(out, rows_arg=0):
    tabs = schema()
    values = {int(k): v for k, v in json.load(open(os.path.join(HERE, 'values.json'))).items()}
    slots = {int(k): v for k, v in json.load(open(os.path.join(HERE, 'slotmap.json'))).items()}
    n_ok = 0
    dropped = collections.Counter()
    with open(out, 'w') as f:
        for n in range(1, 23):
            stmts = query(n, values[n], slots[n])
            used = tables_of(stmts, tabs)
            if not used:
                print('query %d reads no known table' % n, file=sys.stderr)
                continue
            lits = literals(stmts)
            colvals = predicates.column_constants(' '.join(stmts))
            rng = random.Random(n)
            rows = rows_arg or max(2, 12 // len(used))
            setup = []
            for t in used:
                cols = tabs[t]
                # clause 1.4.2.2 permits declaring exactly these primary keys
                keys = declared_keys.TPCH.get(t, [])
                setup.append('CREATE TABLE %s (%s)'
                             % (t, ', '.join(['%s %s' % c for c in cols]
                                             + declared_keys.clauses(keys))))
                vals = [[value(c, ty, i, rows, rng, lits, colvals) for c, ty in cols]
                        for i in range(rows)]
                vals, lost = declared_keys.enforce(
                    vals, [c for c, _ in cols], keys,
                    lambda i: value(cols[i][0], cols[i][1], rng.randrange(rows),
                                    rows, rng, lits),
                    fresh=lambda i, ctr, cur, _cols=cols:
                        declared_keys.perturb(cur, ctr, _cols[i][1]))
                if lost:
                    dropped[t] += lost
                setup.append('INSERT INTO %s VALUES %s'
                             % (t, ', '.join('(%s)' % ', '.join(v) for v in vals)))
            # Q15 creates a view, queries it, and drops it: the view belongs to
            # the setup, the drop is left out (the scenario's schema is dropped
            # whole), and what is measured is the select
            views = [s for s in stmts if re.match(r'(?is)\s*create\s+view', s)]
            selects = [s for s in stmts if re.match(r'(?is)\s*select', s)]
            setup += views
            queries = [['%d' % n, s] for s in selects]
            if not queries:
                print('query %d has no select' % n, file=sys.stderr)
                continue
            entry = {'id': 'tpch/%02d' % n, 'setup': setup, 'queries': queries,
                     'original': ';\n'.join(query(n, values[n], slots[n], render=False)),
                     'faithful': True}
            notes = [d for _, _, d in RENDERED.get(n, ())]
            if n in KNOWN_DEVIATION:
                notes.append(KNOWN_DEVIATION[n])
            if notes:
                entry['dialect' if n in RENDERED else 'deviation'] = ' '.join(notes)
            f.write(json.dumps(entry) + '\n')
            n_ok += 1

    print('%d entries written for the 22 queries' % n_ok)
    if dropped:
        print('rows dropped to satisfy a declared key:', dict(dropped))
    verify(out)


def cli():
    import argparse
    ap = argparse.ArgumentParser(
        description='Build TPC-H scenarios from a TPC-H kit.',
        epilog="Obtain the kit from https://www.tpc.org/ (TPC-H Tools). --kit is the "
               "dbgen directory of the download: it must hold dss.ddl and "
               "queries/<n>.sql, the 22 query templates. The substitution values this "
               "corpus uses ship beside this script as values.json and slotmap.json, "
               "so qgen is not needed and the queries come out identical on every run.")
    ap.add_argument('out', help='output JSONL')
    ap.add_argument('--kit', required=True, help="the kit's dbgen directory")
    ap.add_argument('--rows', type=int, default=0, help='rows per table (0 = default)')
    a = ap.parse_args()
    global KIT
    KIT = a.kit
    for p in (os.path.join(KIT, 'dss.ddl'), os.path.join(KIT, 'queries')):
        if not os.path.exists(p):
            ap.error('not found under --kit: %s' % p)
    return main(a.out, a.rows)


if __name__ == '__main__':
    sys.exit(cli())
