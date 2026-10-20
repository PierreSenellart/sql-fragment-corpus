"""Scenarios from the 99 TPC-DS query templates, as the official generator writes them.

  python3 tpcds_extract.py OUT.jsonl --schema TPCDS.SQL --queries DIR

The query text comes from `dsqgen`, the specification's own query generator,
built from the TPC-DS kit and run with

  dsqgen -DIRECTORY query_templates -INPUT query_templates/templates.lst \\
         -DIALECT netezza -QUALIFY Y -SCALE 1

`-QUALIFY Y` asks for the **qualification queries**, the generator's documented
set with its substitution parameters resolved in ascending order, which is the
TPC-DS counterpart of the validation parameters used for TPC-H. `-DIALECT
netezza` is chosen among the kit's own dialect files because it is the one whose
row limit is spelled `limit %d`, which is PostgreSQL's spelling; `ansi.tpl`
spells it `top %d`. So the dialect choice is the kit's, not an invention.

Four templates (14, 23, 24, 39) hold two statements each, hence 103 queries.

**Three changes are applied**, and nothing else:

  * a dialect rendering. TPC-DS writes a date interval as
    `(cast('2000-08-19' as date) + 14 days)`, which PostgreSQL has no grammar
    for. `14 days` becomes `interval '14 days'`, which is what the text means and
    what PostgreSQL computes; 18 of the 99 templates need it, and each is marked
    in the `dialect` field, as the TPC-H script marks its own.
  * a change to the query text, in templates 36, 70 and 86, marked in the
    `altered` field: see ALIAS_IN_ORDER_BY below.
  * the LIMIT values the corpus uses, for 12 queries whose own LIMIT is at least
    the number of rows they return on the generated instance: see
    `support/limits.py` and `limits.json`.

An entry's `original` keeps the statements as dsqgen wrote them.

The data is generated rather than taken at a scale factor: the corpus needs a
small instance a query can be checked against. Values are drawn from
each query's own literals so that its predicates select something, and a column
whose name ends in `_sk` (TPC-DS's surrogate keys) is drawn from a small shared
range so that joins match.
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
import limits

# set from the command line: see main()
SCHEMA_SQL = None
QUERY_DIR = None

# the one dialect rendering, with its reason
DAYS = (re.compile(r'(\d+)\s+days\b', re.I), r"interval '\1 days'")
DIALECT_NOTE = ("a date interval written \"+ N days\", which PostgreSQL has no "
                "grammar for, is rendered \"+ interval 'N days'\": the same "
                "quantity, spelled as PostgreSQL spells it.")

# An ALTERATION, not a dialect rendering, and marked as one per entry. TPC-DS
# defines an output column
#     ,grouping(i_category)+grouping(i_class) as lochierarchy
# and then uses the name inside an ORDER BY expression:
#     order by lochierarchy desc, case when lochierarchy = 0 then i_category end
# PostgreSQL resolves a bare output name in ORDER BY, so "lochierarchy desc" is
# fine, but inside an expression the name must be an input column, so the CASE
# term fails with 42703. Every PostgreSQL port of TPC-DS inlines the alias's
# defining expression there, which is the substitution PostgreSQL would make if
# it resolved the name, and which changes the query text rather than its dialect.
# Queries 36, 70 and 86; without it they leave the corpus entirely, and they are
# the benchmark's only rank() over a ROLLUP with grouping() in the partition,
# which is close to what this corpus is about. The changes are the minimum for
# PostgreSQL support; the same rewrite is wanted for
# the entries to be marked altered.
ALIAS_IN_ORDER_BY = re.compile(r'(?is),\s*(grouping\([^()]*\)\s*\+\s*grouping\([^()]*\))\s+as\s+(\w+)')
ALTERED_NOTE = ("an output-column name used inside an ORDER BY expression is replaced "
                "by its defining expression, which PostgreSQL cannot resolve there "
                "(42703). The query text changes, not only its dialect.")


def inline_order_by_alias(sql):
    """(sql, changed): inline the alias where it sits inside an ORDER BY term."""
    m = ALIAS_IN_ORDER_BY.search(sql)
    if not m:
        return sql, False
    expr, alias = m.group(1), m.group(2)
    i = sql.lower().rfind('order by')
    if i < 0:
        return sql, False
    head, tail = sql[:i], sql[i:]
    out, changed = [], False
    for term in re.split(r',(?![^()]*\))', tail):
        bare = re.sub(r'(?i)\b(asc|desc)\b|\border\s+by\b|\blimit\s+\d+\b', '', term).strip()
        if bare.lower() != alias.lower() and re.search(r'(?i)\b%s\b' % re.escape(alias), term):
            term = re.sub(r'(?i)\b%s\b' % re.escape(alias), '(%s)' % expr, term)
            changed = True
        out.append(term)
    return head + ','.join(out), changed


TYPES = {'integer': 'integer', 'int': 'integer', 'char': 'char', 'varchar': 'varchar',
         'decimal': 'numeric', 'date': 'date', 'time': 'time'}


def schema():
    """The 25 TPC-DS tables, from the kit's own tpcds.sql."""
    text = open(SCHEMA_SQL).read()
    text = re.sub(r'^\s*--.*$', '', text, flags=re.M)
    tables = {}
    for m in re.finditer(r'create table\s+(\w+)\s*\((.*?)\)\s*;', text, re.S | re.I):
        cols = []
        for part in re.split(r',\s*(?=\w+\s+\w)', m.group(2)):
            part = ' '.join(part.split())
            if not part or re.match(r'(?i)(primary|foreign|constraint)\b', part):
                continue
            bits = part.split()
            if len(bits) < 2:
                continue
            name = bits[0].lower()
            base = re.match(r'(?i)([a-z]+)\s*(\((\d+)(\s*,\s*(\d+))?\))?', ' '.join(bits[1:]))
            kind = TYPES.get(base.group(1).lower())
            if kind is None:
                continue
            if kind in ('char', 'varchar'):
                kind = '%s(%s)' % (kind, base.group(3) or '1')
            elif kind == 'numeric' and base.group(3):
                kind = 'numeric(%s,%s)' % (base.group(3), base.group(5) or '0')
            cols.append((name, kind))
        if cols:
            tables[m.group(1).lower()] = cols
    return tables


def queries():
    """(template number, statements, the kit's statements, rendered, altered)
    for each of the 99 templates."""
    out = []
    for n in range(1, 100):
        path = os.path.join(QUERY_DIR, '%d.sql' % n)
        text = open(path).read()
        days = DAYS[0].sub(DAYS[1], text)
        rendered, altered = inline_order_by_alias(days)
        stmts = [' '.join(s.split(chr(10))) and s.strip()
                 for s in rendered.split(';') if s.strip()]
        kit = [s.strip() for s in text.split(';') if s.strip()]
        # `dialect` says that the interval was rendered, so it is set only where
        # that rendering applied, not wherever the text changed: the alias
        # inlining has its own field, `altered`
        out.append((n, stmts, kit, days != text, altered))
    return out


def tables_of(stmts, known):
    """Which tables the statements read. TPC-DS aliases heavily, so a name is
    taken only where it stands as a relation: after FROM, a comma or a JOIN."""
    text = ' '.join(stmts).lower()
    found = set()
    for m in re.finditer(r'(?:from|join|,)\s*([a-z_][a-z_0-9]*)', text):
        if m.group(1) in known:
            found.add(m.group(1))
    return sorted(found)


def literals(stmts):
    lits = {'s': set(), 'n': set(), 'd': set()}
    text = ' '.join(stmts)
    for m in re.finditer(r"'([^']*)'", text):
        v = m.group(1)
        if re.match(r'^\d{4}-\d{2}-\d{2}$', v):
            lits['d'].add(v)
        elif re.match(r'^\d+ days?$', v):
            continue
        elif v.strip():
            lits['s'].add(v.strip()[:40])
    for m in re.finditer(r'(?<![\w.\'])(\d+(?:\.\d+)?)(?![\w.\'])', text):
        try:
            lits['n'].add(float(m.group(1)))
        except ValueError:
            pass
    return lits


from predicates import column_constants


def value(col, ty, rows, rng, lits, row=None, colvals=None):
    if col.endswith('_sk'):
        return str(rng.randint(1, max(2, rows)))
    if ty.startswith('date'):
        pool = sorted(lits['d']) or ['2000-01-01']
        base = rng.choice(pool)
        y, mo, d = (int(x) for x in base.split('-'))
        d = min(28, max(1, d + rng.choice((0, 1, 3, 10))))
        return "'%04d-%02d-%02d'" % (y, mo, d)
    if ty.startswith('numeric'):
        pool = [x for x in lits['n'] if 0 < x < 10000] or [10.0]
        v = rng.choice(sorted(pool)) * rng.choice((0.5, 1.0, 2.0))
        # clamp to what the column can actually hold. Without this the literals
        # taken from the queries (up to 10,000, doubled) overflowed the kit's own
        # declarations -- s_gmt_offset is numeric(5,2), so 999.99 is its maximum
        # and 4000.00 raises 22003. The INSERT then failed and the table stayed
        # empty, so the scenario's queries ran over no rows: 25 of the 99
        # scenarios were in that state, answering nothing and looking correct.
        m = re.search(r'numeric\((\d+)\s*,\s*(\d+)\)', ty)
        if m:
            prec, scale = int(m.group(1)), int(m.group(2))
            cap = float(10 ** (prec - scale)) - 10.0 ** -min(scale, 2)
            v = min(v, cap)
        return '%.2f' % v
    if ty.startswith('integer'):
        pool = (sorted({int(x) for x in lits['n'] if 0 <= x < 3000}) or [1, 2]) + [1, 2]
        # Deal the query's own constants across the rows instead of drawing each
        # independently. With a handful of rows an independent draw covers some of
        # a query's predicates and misses others: query 90 counts web_sales in two
        # hour windows, 12..13 and 14..15, and its time_dim came out with hours 2
        # and 12 -- the second count was 0 and the ratio divided by zero. Dealing
        # gives each column its own rotation through the pool (a stable offset from
        # the column name, not hash(), which is salted per process), so a row's
        # columns do not all take the same value.
        own = sorted((colvals or {}).get(col.lower(), ()))
        if own and row is not None:
            # the column's own constants first, so each is present in some row
            if row < len(own):
                return str(own[row])
            pool = own + pool
        if row is not None:
            off = sum(col.encode()) if col else 0
            return str(pool[(row + off) % len(pool)])
        return str(rng.choice(pool))
    if ty.startswith('time'):
        return "'%02d:00:00'" % rng.randint(0, 23)
    words = sorted(lits['s']) or ['foo']
    n = int(re.search(r'\((\d+)\)', ty).group(1))
    return "'%s'" % rng.choice(words).replace("'", "''")[:n]


LIMITS = limits.table()


def main(out, rows_arg=0):
    tabs = schema()
    pks = declared_keys.tpcds(open(SCHEMA_SQL).read())
    dropped = collections.Counter()
    n_ok = 0
    with open(out, 'w') as f:
        for n, stmts, kit, rendered, altered in queries():
            used = tables_of(stmts, tabs)
            if not used:
                print('template %d reads no known table' % n, file=sys.stderr)
                continue
            lits = literals(stmts)
            colvals = column_constants(' '.join(stmts))
            rng = random.Random(n)
            rows = rows_arg or max(2, 16 // len(used))
            setup = []
            for t in used:
                cols = tabs[t]
                # the kit's own tpcds.sql declares these; schema() skips the clause
                keys = pks.get(t, [])
                setup.append('CREATE TABLE %s (%s)'
                             % (t, ', '.join(['%s %s' % c for c in cols]
                                             + declared_keys.clauses(keys))))
                need = max([len(colvals.get(c.lower(), ())) for c, _ in cols] + [0])
                nrows = max(rows, min(need, 8))
                vals = [[value(c, ty, rows, rng, lits, row=i, colvals=colvals)
                         for c, ty in cols] for i in range(nrows)]
                vals, lost = declared_keys.enforce(
                    vals, [c for c, _ in cols], keys,
                    lambda i: value(cols[i][0], cols[i][1], rows, rng, lits),
                    fresh=lambda i, ctr, cur, _cols=cols:
                        declared_keys.perturb(cur, ctr, _cols[i][1]))
                if lost:
                    dropped[t] += lost
                setup.append('INSERT INTO %s VALUES %s'
                             % (t, ', '.join('(%s)' % ', '.join(v) for v in vals)))
            # the LIMIT the corpus uses, where it differs from the kit's
            lim = LIMITS.get('tpcds/%02d' % n, {})
            qs = [['%d-%d' % (n, k), s] for k, s in enumerate(stmts)]
            qs = [[q, limits.apply(s, *lim[q]) if q in lim else s] for q, s in qs]
            entry = {'id': 'tpcds/%02d' % n, 'setup': setup,
                     'queries': qs,
                     'original': ';\n'.join(kit), 'faithful': True}
            if rendered:
                entry['dialect'] = DIALECT_NOTE
            if altered:
                entry['faithful'] = False
                entry['altered'] = ALTERED_NOTE
            f.write(json.dumps(entry) + '\n')
            n_ok += 1
    print('%d of 99 templates written, %d queries'
          % (n_ok, sum(len(json.loads(l)['queries']) for l in open(out))))
    if dropped:
        print('rows dropped to satisfy a declared key:',
              dict(dropped.most_common(8)), 'total', sum(dropped.values()))


def cli():
    import argparse
    ap = argparse.ArgumentParser(
        description='Build TPC-DS scenarios from a TPC-DS kit.',
        epilog='Obtain the kit from https://www.tpc.org/ (TPC-DS Tools). Generate the '
               'queries with dsqgen, which writes one file per template, then point '
               '--queries at the directory holding them. --schema is the kit\'s '
               'tpcds.sql (tools/tpcds.sql in the download).')
    ap.add_argument('out', help='output JSONL')
    ap.add_argument('--schema', required=True, help="the kit's tpcds.sql")
    ap.add_argument('--queries', required=True,
                    help='directory of generated queries, named <n>.sql')
    ap.add_argument('--rows', type=int, default=0, help='rows per table (0 = default)')
    a = ap.parse_args()
    global SCHEMA_SQL, QUERY_DIR
    SCHEMA_SQL, QUERY_DIR = a.schema, a.queries
    for p in (SCHEMA_SQL, QUERY_DIR):
        if not os.path.exists(p):
            ap.error('no such path: %s' % p)
    return main(a.out, a.rows)


if __name__ == '__main__':
    sys.exit(cli())
