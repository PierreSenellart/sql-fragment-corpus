"""Scenarios from SQLShare (data release 1): the queries of scientists over their
own uploaded files. One scenario per set of base datasets read: the tables,
filled with a sample of the rows of the uploaded files, the views they go
through, and the queries. Everything is translated from T-SQL by sqlglot, with
all identifiers lowercased (T-SQL is case-insensitive)."""

import collections
import csv
import hashlib
import io
import json
import os
import random
import re
import sys
import zipfile

# the helper modules ship beside this script, in support/
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'support'))

import sandbox
import sqlglot

import limits
import pin_moment
import tsql_renderings
from sqlglot import exp

from translation_fidelity import untranslatable, unfilled_placeholder

SKIPPED = collections.Counter()
RENDER_NOTES = set()      # the renderings that fired for the entry being built
ALL_NOTES = set()         # every rendering that fired in the build, for the summary

sqlglot.logger.setLevel('ERROR')
csv.field_size_limit(1 << 24)

ROOT = 'sqlshare_data_release1/'
# set from the command line: see cli()
ZIP = None
URL = 'https://shrquerylogs.s3.amazonaws.com/public/sqlshare_data_release1.zip'
REF = re.compile(r'\[([^\]]+)\]\s*\.\s*\[([^\]]+)\]')
ROWS = 30
READ_LINES = 2000


def tname(u, n):
    return 't_' + hashlib.md5(('%s/%s' % (u, n)).lower().encode()).hexdigest()[:10]


def rename_refs(sql):
    return REF.sub(lambda m: '[%s]' % tname(m.group(1), m.group(2)), sql)


def lower_idents(tree):
    for node in tree.find_all(exp.Identifier):
        node.set('this', node.this.lower())
    return tree


def to_pg(sql):
    trees = sqlglot.parse(rename_refs(sql), read='tsql')
    out = []
    for t in trees:
        if t is None:
            continue
        why = untranslatable(t)
        if why:
            # not a translation: the clause would be dropped and the entry would
            # hold a query nobody wrote (see support/translation_fidelity.py)
            SKIPPED[why] += 1
            continue
        emitted, rnotes = tsql_renderings.render(lower_idents(t).sql(dialect='postgres'))
        if rnotes:
            RENDER_NOTES.update(rnotes)
        ph = unfilled_placeholder(emitted)
        if ph:
            SKIPPED['placeholder-not-filled ' + ph[0]] += 1
            continue
        out.append(emitted)
    return out


def parse_value(v):
    v = v.strip()
    if v == '' or v.upper() == 'NULL':
        return None
    return v


def infer(col):
    vals = [v for v in col if v is not None]
    if not vals:
        return 'text'
    try:
        if all(abs(int(v)) < 2 ** 62 for v in vals):
            return 'bigint'
    except ValueError:
        pass
    try:
        [float(v) for v in vals]
        return 'double precision'
    except ValueError:
        return 'text'


def lit(v, typ):
    if v is None:
        return 'NULL'
    if typ == 'double precision' and v.lower() in ('nan', 'inf', '-inf', 'infinity', '-infinity'):
        return "'%s'" % v
    if typ != 'text':
        return v
    return "'" + v.replace("'", "''") + "'"


# `"col" = 'na'`, `"col" IN ('a','b')`, `"col" LIKE 'x%'`: the author treated the column
# as text, and the CSV data is text, so a sample that happened to parse as numbers is
# what is wrong. Typing such a column text is what lets a view comparing it with a
# string create; otherwise the literal cannot be cast to the inferred numeric type.
# The same rule as the BEAVER builder's `must_be_text`.
TEXT_COMPARISON = re.compile(
    r"""(?isx) (?: \[ ([^\]]+) \] | " ([^"]+) " | \b([a-z_][\w]*) \b )
        \s* (?: = | <> | != | \blike\b | \bin\b \s* \( )
        \s* N? ' ([^']*) ' """)
NUMERIC_LITERAL = re.compile(r'^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$')


def text_columns(texts):
    """Column names compared with a literal that is not a number."""
    out = set()
    for t in texts:
        for m in TEXT_COMPARISON.finditer(t or ''):
            name = m.group(1) or m.group(2) or m.group(3)
            lit = m.group(4)
            if name and lit and not NUMERIC_LITERAL.match(lit.strip()):
                out.add(name.strip().lower())
    return out


# a column the group's queries do arithmetic on, or aggregate numerically
ARITH = re.compile(r'(?i)(?:\b(\w+)\s*[-+*/]\s*[\w.\'"]|[\w.\'"]\s*[-+*/]\s*\b(\w+)\b'
                   r'|\b(?:sum|avg|stddev|variance|var_samp|var_pop)\s*\(\s*(?:\w+\.)?(\w+)\s*\))')


def numeric_columns(texts):
    """Column names a query treats as numbers.

    Such a column is not promoted to text to make a unioning view create: the
    query's own arithmetic would then fail, with errors like
    `double precision + text`. The conflict is in the data -- SQLShare writes a missing number
    as `NA`, so one upload of a split dataset is text and another numeric -- and no
    single type serves both the view and the arithmetic. Where a query needs the
    number, the number wins and the view stays broken.
    """
    out = set()
    for t in texts:
        for m in ARITH.finditer(t or ''):
            for g in m.groups():
                if g:
                    out.add(g.strip().lower())
    return out


def resolve(lower_names, u, n):
    """The archive path of dataset [u].[n], or None.

    A dataset is a view over [user].[table_<file>], or the file itself, or -- when
    it was a VIEW in SQLShare -- a snapshot named `materialized_<something>.<NAME>`.
    This lookup decides whether a scenario is built at all, so it lives here and
    not only inside load_table: the type-unification pass in `main` needs the same
    answer.
    """
    p = lower_names.get(('%sdata/%s/%s' % (ROOT, u, n)).lower()) if n.startswith('table_') else None
    p = p or lower_names.get(('%sdata/%s/table_%s' % (ROOT, u, n)).lower())
    if p is None:
        pref = ('%sdata/%s/materialized_' % (ROOT, u)).lower()
        tail = ('.%s' % n).lower()
        cands = sorted(k for k in lower_names
                       if k.startswith(pref) and k.endswith(tail))
        p = lower_names[cands[0]] if cands else None
    return p


def real_name(p, n):
    return p.split('/table_', 1)[1] if not n.startswith('table_') else p.rsplit('/', 1)[1]


def load_table(z, names, u, n, rng, force_text=(), capture=None, force_type=None):
    """CREATE TABLE and INSERT statements for dataset [u].[n], or None."""
    path = '%sdata/%s/table_%s' % (ROOT, u, n)
    if path not in names:
        path = '%sdata/%s/%s' % (ROOT, u, n)
    if path not in names:
        # A dataset that was a VIEW in SQLShare ships its data as a snapshot named
        # `materialized_<something>.<NAME>`, not `table_<NAME>`. The release has 3,230
        # `table_` files and 94 `materialized_` ones.
        tail = ('.%s' % n).lower()
        pref = ('%sdata/%s/materialized_' % (ROOT, u)).lower()
        cands = [x for x in names if x.lower().startswith(pref) and x.lower().endswith(tail)]
        if not cands:
            return None
        path = sorted(cands)[0]
    with z.open(path) as f:
        head = []
        for i, line in enumerate(io.TextIOWrapper(f, encoding='utf-8', errors='replace')):
            head.append(line)
            if i >= READ_LINES:
                break
    if not head:
        return None
    delim = '\t' if head[0].count('\t') > head[0].count(',') else ','
    try:
        rows = list(csv.reader(head, delimiter=delim))
    except csv.Error:
        return None
    header, body = rows[0], [r for r in rows[1:] if r]
    ncol = len(header)
    body = [r for r in body if len(r) == ncol]
    if ncol == 0 or not body:
        return None
    # The column types are inferred from EVERY row read, not from the sample that gets
    # inserted: SQLShare's CSVs write a missing number as `NA`, and a sample that
    # misses it would type the column double precision while a view over it compares
    # with 'NA', so the view would fail to create.
    all_rows = [[parse_value(v) for v in r] for r in body]
    if len(body) > ROWS:
        body = rng.sample(body, ROWS)
    cols = [re.sub(r'\s+', ' ', h.strip().strip('"')).lower() or 'column%d' % i for i, h in enumerate(header)]
    seen = collections.Counter()
    for i, c in enumerate(cols):
        seen[c] += 1
        if seen[c] > 1:
            cols[i] = '%s_%d' % (c, seen[c])
    data = [[parse_value(v) for v in r] for r in body]
    types = [infer([r[i] for r in all_rows]) for i in range(ncol)]
    for i, c in enumerate(cols):
        if c in force_text:
            types[i] = 'text'
        if force_type and c in force_type:
            types[i] = force_type[c]
    t = tname(u, n)
    create = 'CREATE TABLE %s (%s)' % (t, ', '.join('"%s" %s' % (c.replace('"', '""')[:63], ty)
                                                  for c, ty in zip(cols, types)))
    insert = 'INSERT INTO %s VALUES %s' % (t, ', '.join(
        '(%s)' % ', '.join(lit(v, ty) for v, ty in zip(r, types)) for r in data))
    if capture is not None:
        capture[(u, n)] = (list(cols), list(types))
    return [create, insert]


LIMITS = limits.table()


def main(out):
    z = zipfile.ZipFile(ZIP)
    # every input comes from the archive, which is read rather than unpacked: it
    # is 3.6 GB and only a few hundred kilobytes of it are needed
    def member(name):
        with z.open(ROOT + name) as fh:
            return fh.read().decode('utf-8', 'replace')
    txt = member('queries.txt')
    queries = list(dict.fromkeys(q.strip() for q in re.split(r'\n_{20,}\n', txt) if q.strip()))
    vtxt = member('view_script.txt')
    views = {}
    for m in re.finditer(r'(?is)create\s+view\s+\[([^\]]+)\]\.\[([^\]]+)\](.*?)(?=\bcreate\s+view\s+\[|\Z)', vtxt):
        # view_script.txt separates definitions with a line of underscores,
        # and the capture runs to the next "create view": left in, sqlglot
        # reads the separator as a table alias where it parses at all, and
        # returns the statement untranslated where it does not, so the view is
        # never created and every query over it fails on a missing relation
        text = re.split(r'\n_{10,}', m.group(0))[0]
        views[(m.group(1).lower(), m.group(2).lower())] = text.strip().rstrip(';').strip()
    names = set(z.namelist())
    lower_names = {p.lower(): p for p in names}

    def base_and_views(ref, acc_b, acc_v, depth=0):
        key = (ref[0].lower(), ref[1].lower())
        if key in views and depth < 10:
            if key not in acc_v:
                acc_v[key] = None
                # the first reference is the name of the view itself
                for r in REF.findall(views[key])[1:]:
                    base_and_views(r, acc_b, acc_v, depth + 1)
                acc_v.pop(key)
                acc_v[key] = views[key]
            return
        acc_b.add(key)

    groups = collections.OrderedDict()
    for q in queries:
        refs = REF.findall(q)
        if not refs:
            continue
        b, v = set(), collections.OrderedDict()
        for r in refs:
            base_and_views(r, b, v)
        k = (tuple(sorted(b)), tuple(v))
        groups.setdefault(k, []).append(q)
    rng = random.Random(0)
    n_s = n_q = 0
    with open(out, 'w') as f:
        for (bases, vkeys), qs in groups.items():
            setup, ok = [], True
            # computed from the group's own queries and the definitions of the views
            # they read, before any table is built
            text_cols = text_columns(qs + [views[vk] for vk in vkeys if vk in views])
            # A view that unions several of the group's tables needs their columns
            # to agree in type, and the tables are inferred one by one: when one
            # upload of a split dataset has an `NA` in a column and another does
            # not, one comes out text and the other double precision, and
            # `CREATE VIEW ... SELECT * FROM a UNION ALL SELECT * FROM b` fails
            # with 42804. So the group is built twice: the first pass only to see the types, and any column a
            # unioning view reads whose type is not the same in every table it
            # unions is forced to text, which every value can be.
            force_type = {}
            unioning = any(re.search(r'(?i)\bunion\b', views[vk] or '')
                           for vk in vkeys if vk in views)
            if unioning and len(bases) > 1:
                seen_types = {}
                for u, n in bases:
                    p0 = resolve(lower_names, u, n)
                    if p0 is None:
                        continue
                    try:
                        load_table(z, names, u, real_name(p0, n), random.Random(0),
                                   force_text=text_cols, capture=seen_types)
                    except Exception:
                        pass
                # positionally, NOT by name: `SELECT *` matches the unioned
                # tables column by position, and these split uploads carry
                # different headers.
                per_pos = collections.defaultdict(set)
                for cols_, types_ in seen_types.values():
                    for i, ty in enumerate(types_):
                        per_pos[i].add(ty)
                # promote to the most general of the types seen, which is NOT
                # always text: columns that differ only between bigint and double
                # precision become double precision, so that `x + 1.5` and
                # `sum(x)` still work, and text is used only where one of the
                # tables really has text.
                rank = {'bigint': 0, 'double precision': 1, 'text': 2}
                target = {i: max(tys, key=lambda t: rank.get(t, 2))
                          for i, tys in per_pos.items() if len(tys) > 1}
                num_cols = numeric_columns(qs + [views[vk] for vk in vkeys if vk in views])
                force_type = {}
                for cols_, _ in seen_types.values():
                    for i, ty in target.items():
                        if i < len(cols_):
                            c = cols_[i]
                            # a column a query does arithmetic on is not made text
                            if ty == 'text' and c.lower() in num_cols:
                                continue
                            force_type[c] = ty
            for u, n in bases:
                p = resolve(lower_names, u, n)
                if p is None:
                    ok = False
                    break
                real_n = real_name(p, n)
                st = load_table(z, names, u, real_n, rng, force_text=text_cols,
                                force_type=force_type)
                if st is None:
                    ok = False
                    break
                # the table name must not depend on the case used in the query
                st[0] = st[0].replace(tname(u, real_n), tname(u, n), 1)
                st[1] = st[1].replace(tname(u, real_n), tname(u, n), 1)
                setup += st
            if not ok:
                continue
            for vk in vkeys:
                try:
                    setup += to_pg(views[vk])
                except Exception:
                    pass
            pq = []
            orig = {}
            # per ENTRY, not globally: a set accumulated across the whole build records
            # a rendering only against the first entry that needed it, which marked 3 of
            # 1,929 entries where the real figure is far larger
            RENDER_NOTES.clear()
            for i, q in enumerate(qs):
                try:
                    t = to_pg(q)
                except Exception:
                    continue
                if len(t) != 1:
                    continue
                # a query slot must hold a query: the release's file also contains
                # DELETE, INSERT, UPDATE and ALTER statements, which change the
                # state of the database rather than reporting on it
                # the next query in the same scenario reads. The DBA builder already
                # separates setup from queries by parse-node kind; this did not.
                try:
                    kind = sandbox.analyse(t[0])[0]
                except Exception:
                    SKIPPED['unparsed by the server parser'] += 1
                    continue
                if kind != 'SelectStmt':
                    SKIPPED['not a query: %s' % kind] += 1
                    continue
                pq.append(('%d' % i, t[0]))
                orig['%d' % i] = q              # the original T-SQL, for joining
            if pq:
                n_s += 1
                n_q += len(pq)
                sid = 'sqlshare/' + hashlib.md5(repr((bases, vkeys)).encode()).hexdigest()[:8]
                # the text the corpus holds: the LIMIT it uses where it differs
                # from the source's, and every clock read pinned to one moment
                lim = LIMITS.get(sid, {})
                pq = [(qid, pin_moment.pin(limits.apply(sql, *lim[qid]) if qid in lim
                                           else sql))
                      for qid, sql in pq]
                entry = {'id': sid, 'setup': setup, 'queries': pq, 'originals': orig}
                # which T-SQL renderings this entry needed: a reader can see that a
                # query was changed to run at all, and the isnumeric one says so loudly
                # because it can select different rows than the author saw
                fired = sorted(RENDER_NOTES)
                ALL_NOTES.update(fired)
                if fired:
                    entry['rendered'] = fired
                f.write(json.dumps(entry) + '\n')
    print(n_s, 'scenarios', n_q, 'queries')
    for note in sorted(ALL_NOTES):
        print('   rendered:', note[:100])
    if SKIPPED:
        print('not translated, so left out:', dict(SKIPPED))


def cli():
    import argparse
    ap = argparse.ArgumentParser(
        description='Build SQLShare scenarios from SQLShare Data Release 1.',
        epilog='The release is a single 3.6 GB zip, which this script reads without '
               'unpacking. Download it from %s, or pass --download to fetch it. Its '
               'authors ask that four publications be cited: see NOTICE.md.' % URL)
    ap.add_argument('out', help='output JSONL')
    ap.add_argument('--zip', dest='zippath', default='sqlshare_data_release1.zip',
                    help='path to sqlshare_data_release1.zip (default: ./)')
    ap.add_argument('--download', action='store_true',
                    help='fetch the archive to --zip if it is not there')
    a = ap.parse_args()
    global ZIP
    ZIP = a.zippath
    if a.download and not os.path.exists(ZIP):
        import urllib.request
        print('fetching %s\n     to %s (3.6 GB)' % (URL, ZIP))
        urllib.request.urlretrieve(URL, ZIP)
    if not os.path.exists(ZIP):
        ap.error('%s not found; pass --zip or --download' % ZIP)
    return main(a.out)


if __name__ == '__main__':
    sys.exit(cli())
