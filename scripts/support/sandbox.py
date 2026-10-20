"""Statement filtering, so that code taken from public posts can be replayed.

Every statement is parsed with PostgreSQL's own parser (pglast). Setup
statements are accepted only if of a whitelisted type, queries only if they
are plain SELECTs; in both, any call to a function that reads or writes
outside the database, signals backends, changes settings or runs a query
given as a string rejects the statement.
"""

import json
import re
from pglast import parser

SETUP_TYPES = {
    'CreateStmt', 'InsertStmt', 'UpdateStmt', 'DeleteStmt', 'ViewStmt',
    'IndexStmt', 'CreateEnumStmt', 'CompositeTypeStmt', 'CreateDomainStmt',
    'AlterTableStmt', 'CreateTableAsStmt', 'CreateSeqStmt', 'CommentStmt',
    # A matview's REFRESH recomputes a relation of the scenario's own schema; a
    # setup that refreshes one needs it, or the view keeps the rows it had when it
    # was created.
    'RefreshMatViewStmt',
}

# Functions never allowed, whatever the statement.
DENY_FUNC = re.compile(
    r'^(pg_(?!typeof$|column_size$|size_pretty$).*|lo_.*|dblink.*|set_config|'
    r'query_to_xml.*|table_to_xml.*|cursor_to_xml.*|database_to_xml.*|schema_to_xml.*|'
    r'query_to_xmlschema|currval|nextval|setval|lastval|txid_.*|'
    r'current_setting|inet_.*|version|has_.*_privilege|'
    r'random|random_normal|setseed|gen_random_uuid|uuid_generate_v[14]|clock_timestamp|'
    r'timeofday|statement_timestamp|now|transaction_timestamp|'
    # `format` was denied by association with the query_to_xml family above, which
    # builds or executes SQL. It only formats a string, deterministically given its
    # arguments, and four corpus queries were left unmeasurable for no reason
    # beyond its presence here.
    r'.*_rebuild|ts_stat|xml_is_well_formed.*|'
    r'dsnowball.*|binary_upgrade.*|brin_.*|gin_clean_pending_list|'
    r'provenance)$')

# Functions whose result changes from run to run: allowed in setup, where the
# rows they produce are then fixed, but not in a query.
VOLATILE = re.compile(r'^(random|random_normal|now|clock_timestamp|statement_timestamp|'
                      r'transaction_timestamp|timeofday|gen_random_uuid|nextval)$')
SETUP_ALLOWED_VOLATILE = {'now', 'nextval', 'random', 'gen_random_uuid', 'clock_timestamp',
                          'statement_timestamp', 'transaction_timestamp'}

# Node types that must not appear anywhere.
#
# `LockingClause` (FOR UPDATE / FOR SHARE / FOR NO KEY UPDATE, with or without
# SKIP LOCKED) is not here: it takes row locks and does not change which rows a
# query returns. The replay is a single session per database with nothing else
# holding a lock, so SKIP LOCKED skips nothing and NOWAIT waits for nothing.
DENY_NODES = {'CopyStmt', 'DoStmt', 'CreateFunctionStmt', 'VariableSetStmt',
              'TransactionStmt', 'CallStmt', 'RangeTableSample',
              'NotifyStmt', 'ListenStmt', 'LoadStmt', 'CreateExtensionStmt',
              'CreateTrigStmt', 'RuleStmt', 'GrantStmt', 'GrantRoleStmt',
              'AlterSystemStmt', 'CreateRoleStmt', 'AlterRoleStmt'}

SPECIAL_VALUE = re.compile(r"(?i)'\s*(now|today|tomorrow|yesterday)\s*'")


def walk(node, visit):
    if isinstance(node, dict):
        for k, v in node.items():
            visit(k, v)
            walk(v, visit)
    elif isinstance(node, list):
        for v in node:
            walk(v, visit)


def analyse(sql):
    """Parse `sql` (a single statement). Returns (stmt_type, info) or raises."""
    tree = json.loads(parser.parse_sql_json(sql))
    stmts = tree.get('stmts', [])
    if len(stmts) != 1:
        raise ValueError('not a single statement')
    stmt = stmts[0]['stmt']
    typ = next(iter(stmt))
    info = {'funcs': set(), 'nodes': set(), 'schemas': set(), 'relations': set()}

    def visit(k, v):
        info['nodes'].add(k)
        if k == 'FuncCall':
            name = [n['String']['sval'] for n in v.get('funcname', []) if 'String' in n]
            if name:
                info['funcs'].add(name[-1].lower())
                if len(name) > 1:
                    info['schemas'].add(name[0].lower())
        if k == 'RangeVar':
            if v.get('schemaname'):
                info['schemas'].add(v['schemaname'].lower())
            info['relations'].add(v.get('relname', '').lower())
    walk(stmt, visit)
    return typ, info, stmt


def check_setup(sql):
    """None if the setup statement may run, else the reason."""
    try:
        typ, info, _ = analyse(sql)
    except Exception as e:
        return 'parse: %s' % str(e)[:80]
    if typ not in SETUP_TYPES:
        return 'type %s' % typ
    return _common(info, setup=True)


def check_query(sql):
    """None if the query may run, else the reason."""
    try:
        typ, info, stmt = analyse(sql)
    except Exception as e:
        return 'parse: %s' % str(e)[:80]
    if typ != 'SelectStmt':
        return 'type %s' % typ
    if stmt['SelectStmt'].get('intoClause'):
        return 'SELECT INTO'
    # a data-modifying CTE would change the data under test
    dml = info['nodes'] & {'InsertStmt', 'UpdateStmt', 'DeleteStmt', 'MergeStmt'}
    if dml:
        return 'data-modifying CTE (%s)' % sorted(dml)[0]
    return _common(info, setup=False)


def _common(info, setup):
    bad = info['nodes'] & DENY_NODES
    if bad:
        return 'node %s' % sorted(bad)[0]
    for f in info['funcs']:
        if setup and f in SETUP_ALLOWED_VOLATILE:
            continue
        if DENY_FUNC.match(f):
            return 'function %s' % f
    others = info['schemas'] - {'pg_catalog'}
    if others:
        return 'schema %s' % sorted(others)[0]
    if any(r.startswith('pg_') or r in ('information_schema',) for r in info['relations']):
        return 'catalog relation'
    return None


def features(sql):
    """Constructs of a query, for the classification of the results."""
    try:
        typ, info, stmt = analyse(sql)
    except Exception:
        return set()
    f = set()
    n = info['nodes']
    s = json.dumps(stmt)
    if 'WindowDef' in n or '"over"' in s:
        f.add('window')
    if '"limitCount"' in s or '"limitOffset"' in s:
        f.add('limit')
    if '"distinctClause"' in s:
        f.add('distinct')
    if '"groupClause"' in s:
        f.add('group by')
    if '"havingClause"' in s:
        f.add('having')
    if '"op": "SETOP_UNION"' in s:
        f.add('union')
    if '"op": "SETOP_EXCEPT"' in s:
        f.add('except')
    if '"op": "SETOP_INTERSECT"' in s:
        f.add('intersect')
    if 'SubLink' in n:
        f.add('sublink')
    if '"jointype": "JOIN_LEFT"' in s or '"jointype": "JOIN_RIGHT"' in s or '"jointype": "JOIN_FULL"' in s:
        f.add('outer join')
    if '"withClause"' in s:
        f.add('with')
    if '"recursive": true' in s:
        f.add('recursive')
    if '"lateral": true' in s:
        f.add('lateral')
    if 'GroupingSet' in n:
        f.add('grouping sets')
    aggs = {'count', 'sum', 'avg', 'min', 'max', 'array_agg', 'string_agg', 'bool_and',
            'bool_or', 'every', 'json_agg', 'jsonb_agg', 'stddev', 'variance'}
    if info['funcs'] & aggs:
        f.add('aggregate')
    # CURRENT_TIMESTAMP, CURRENT_DATE… are SQLValueFunction nodes, not calls
    if any(VOLATILE.match(x) for x in info['funcs']) or SPECIAL_VALUE.search(sql) or \
            'SQLValueFunction' in info['nodes']:
        f.add('volatile')
    # results that depend on the physical storage, not on the data
    cols = set()
    def visit(k, v):
        if k == 'ColumnRef':
            for fld in v.get('fields', []):
                if 'String' in fld:
                    cols.add(fld['String']['sval'].lower())
    walk(stmt, visit)
    if cols & {'ctid', 'xmin', 'xmax', 'cmin', 'cmax', 'tableoid'} or \
            info['funcs'] & {'pg_column_size', 'pg_relation_size', 'pg_total_relation_size'}:
        f.add('physical')
    # LIMIT or OFFSET without ORDER BY at some level: SQL leaves the rows unspecified
    def visit2(k, v):
        if k == 'SelectStmt' and (v.get('limitCount') or v.get('limitOffset')) and not v.get('sortClause'):
            f.add('limit without order')
    walk(stmt, visit2)
    if isinstance(stmt.get('SelectStmt'), dict) and (stmt['SelectStmt'].get('limitCount') or
                                                     stmt['SelectStmt'].get('limitOffset')):
        f.add('top-level limit')
    return f


def split(code):
    """Split a code block into statements (PostgreSQL's splitter)."""
    try:
        return [s for s in parser.split(code)]
    except Exception:
        out = []
        for piece in re.split(r';\s*\n', code):
            try:
                out.extend(parser.split(piece))
            except Exception:
                pass
        return out
