"""The keys a source declares, and the code that makes a generated instance respect them.

A script that generates its own rows has to honour the schema's key declarations: an
instance with repeated key values makes a sort order tie where the real schema forbids
one, so a query whose answer is determined looks as though it is not.

Where each corpus's keys come from:

* TPC-H   the specification, clause 1.4.1 ('Primary Key' annotations) and clause
          1.4.2.2, which lists the same columns and permits declaring them:
          "The following columns or set of columns listed in Clause 1.4.1 as
          'Primary Key' may be defined as primary key constraints".  `dss.ddl`
          itself declares none.
* TPC-DS  the kit's own `tpcds.sql`, which declares 24 primary keys, 14 composite.
* BEAVER  its `dev_tables.json`, whose `primary_key` field carries 361 keys over the
          463 tables; the 97 `dw` tables declare none and nothing recovers them.
* SEDE    nothing ships: the Spider file the corpus is built from has
          `primary_keys: []`.  Stack Exchange's schema is public and documents
          every table's `Id` as its primary key, so that is where these come from.

UNIQUE constraints are a separate loss.  The Spider-style JSON BEAVER converted its
schemas into has a slot for primary and foreign keys and none for unique constraints,
so upstream OpenStack's uniqueness -- `uniq_instances0uuid` and 39 others -- vanished
in the conversion rather than being absent from the database.  UPSTREAM_UNIQUE below
is read back out of the services' own SQLAlchemy models, by walking each repository
with `ast` for a class that sets `__tablename__` and collecting its
`UniqueConstraint(…)` column lists and its `Column(…, unique=True)` columns, then
keeping only the tables and columns BEAVER's release actually contains. The
snapshot the keys below were read from, so that they can be checked: opendev.org/
openstack as of 2026-09-27, at nova c280f44, cinder 4823af7, glance d18aef5,
neutron bbb6b2d, keystone c6d3b4e.  The neutron figure is a floor: several
of its tables build `__table_args__` from a helper, and a constraint added by an
alembic migration and not restated in a model is not seen here.
"""
import datetime
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

# --- TPC-H, specification clause 1.4.2.2 -----------------------------------
TPCH = {
    'part':     [['p_partkey']],
    'supplier': [['s_suppkey']],
    'partsupp': [['ps_partkey', 'ps_suppkey']],
    'customer': [['c_custkey']],
    'orders':   [['o_orderkey']],
    'lineitem': [['l_orderkey', 'l_linenumber']],
    'nation':   [['n_nationkey']],
    'region':   [['r_regionkey']],
}

# --- OpenStack, recovered from the services' models (see the module docstring) ---
UPSTREAM_UNIQUE = {
    ('csail_stata_cinder', 'driver_initiator_data'): [['initiator', 'namespace', 'key']],
    ('csail_stata_cinder', 'volume_type_projects'): [['volume_type_id', 'project_id', 'deleted']],
    ('csail_stata_glance', 'image_members'): [['image_id', 'member', 'deleted_at']],
    ('csail_stata_glance', 'image_properties'): [['image_id', 'name']],
    ('csail_stata_glance', 'metadef_namespaces'): [['namespace']],
    ('csail_stata_glance', 'metadef_objects'): [['namespace_id', 'name']],
    ('csail_stata_glance', 'metadef_properties'): [['namespace_id', 'name']],
    ('csail_stata_glance', 'metadef_resource_types'): [['name']],
    ('csail_stata_glance', 'metadef_tags'): [['namespace_id', 'name']],
    ('csail_stata_neutron', 'ha_router_agent_port_bindings'): [['router_id', 'l3_agent_id']],
    ('csail_stata_neutron', 'ml2_geneve_endpoints'): [['host']],
    ('csail_stata_neutron', 'ml2_gre_endpoints'): [['host']],
    ('csail_stata_neutron', 'ml2_vxlan_endpoints'): [['host']],
    ('csail_stata_nova', 'aggregate_hosts'): [['host', 'aggregate_id']],
    ('csail_stata_nova', 'aggregate_metadata'): [['aggregate_id', 'key']],
    ('csail_stata_nova', 'aggregates'): [['name']],
    ('csail_stata_nova', 'compute_nodes'): [['host', 'hypervisor_hostname', 'deleted']],
    ('csail_stata_nova', 'instance_groups'): [['uuid']],
    ('csail_stata_nova', 'instance_info_caches'): [['instance_uuid']],
    ('csail_stata_nova', 'instances'): [['uuid']],
    ('csail_stata_nova', 'key_pairs'): [['user_id', 'name']],
    ('csail_stata_nova', 'pci_devices'): [['compute_node_id', 'address', 'deleted']],
    ('csail_stata_nova', 'project_user_quotas'): [['user_id', 'project_id', 'resource'],
                                                 ['user_id', 'project_id', 'resource', 'deleted']],
    ('csail_stata_nova', 'quotas'): [['project_id', 'resource'],
                                     ['project_id', 'resource', 'deleted']],
    ('csail_stata_nova', 'security_groups'): [['project_id', 'name', 'deleted']],
    ('csail_stata_nova', 'services'): [['host', 'binary', 'deleted'],
                                       ['host', 'topic', 'deleted']],
    ('csail_stata_nova', 'task_log'): [['task_name', 'host', 'period_beginning',
                                        'period_ending']],
    ('csail_stata_nova', 'virtual_interfaces'): [['address', 'deleted']],
    ('keystone', 'federated_user'): [['idp_id', 'protocol_id', 'unique_id']],
    ('keystone', 'group'): [['domain_id', 'name']],
    ('keystone', 'id_mapping'): [['domain_id', 'local_id', 'entity_type']],
    ('keystone', 'local_user'): [['domain_id', 'name'], ['user_id']],
    ('keystone', 'policy_association'): [['endpoint_id', 'service_id', 'region_id']],
    ('keystone', 'project'): [['domain_id', 'name']],
    ('keystone', 'role'): [['name', 'domain_id']],
    ('keystone', 'trust'): [['trustor_user_id', 'trustee_user_id', 'project_id',
                             'impersonation', 'expires_at']],
}


# TPC-DS gives each dimension a surrogate key (*_sk, which the DDL declares) and a
# business key (*_id, which it does not). For date_dim the business key is a calendar
# date and the table holds one row per date, so `d_date` is a candidate key -- and
# the kit's own queries rely on it: query 58 reads
# `(select d_week_seq from date_dim where d_date = '2001-06-16')` as a scalar
# subquery. Nothing declared it, so the generator produced duplicates: 58 of the 89
# date_dim instances had one, which made query 58 raise 21000 and made every other
# query joining date_dim on a date multiply its rows silently. Only date_dim is
# listed: the slowly-changing dimensions (item, store, call_center, web_page,
# web_site, promotion) legitimately repeat their business key across date ranges, so
# a blanket rule over *_id would be wrong.
TPCDS_EXTRA = {'date_dim': [['d_date']]}


def tpcds(text):
    """The kit's declared primary keys, from the same DDL the builder parses,
    plus a candidate key the DDL omits and the kit's queries assume."""
    out = {}
    for m in re.finditer(r'create table\s+(\w+)\s*\((.*?)\)\s*;', text, re.S | re.I):
        for pk in re.finditer(r'(?i)primary key\s*\(([^)]*)\)', m.group(2)):
            out.setdefault(m.group(1).lower(), []).append(
                [c.strip().lower() for c in pk.group(1).split(',')])
    for t, keys in TPCDS_EXTRA.items():
        if t in out:
            for k in keys:
                if k not in out[t]:
                    out[t].append(k)
    return out


def beaver(path=None):
    """(db, table) -> list of key column lists: the declared primary key first, then
    any upstream unique constraint the conversion to Spider JSON dropped."""
    path = path or os.path.join(HERE, 'corpora', 'beaver', 'dev_tables.json')
    out = {}
    for v in json.load(open(path)).values():
        t = (v['db_id'], v['table_name_original'].lower())
        cols = {c.lower() for c in v['column_names_original']}
        keys = []
        pk = [c.lower() for c in (v.get('primary_key') or [])]
        if pk and all(c in cols for c in pk):
            keys.append(pk)
        for k in UPSTREAM_UNIQUE.get(t, []):
            if all(c in cols for c in k) and k not in keys:
                keys.append(k)
        if keys:
            out[t] = keys
    return out


# the one SEDE table with no `Id`: a post carries a tag at most once, so the pair is
# the key, which comes from the data model rather than from a column called Id
SEDE_EXTRA = {'posttags': [['postid', 'tagid']]}


def clauses(keys, quote=False):
    """The table constraints for one table's key list: the first is the primary key,
    the rest are unique constraints, which is how the sources declare them."""
    out = []
    for i, k in enumerate(keys):
        cols = ', '.join('"%s"' % c if quote else c for c in k)
        out.append('%s (%s)' % ('PRIMARY KEY' if i == 0 else 'UNIQUE', cols))
    return out


def perturb(lit, ctr, ty=''):
    """A value of the same shape as `lit` and distinct from it, for a key column whose
    own generator draws from a domain smaller than the number of rows.  Staying close
    to the generated value matters: a key column is often also compared with one of
    the query's literals, and a wild value would only turn a selective predicate into
    an empty answer.  A boolean cannot be perturbed and is returned unchanged, so a
    key that is entirely boolean columns still loses rows."""
    s = str(lit).strip()
    m = re.fullmatch(r"'(\d{4}-\d{2}-\d{2})( \d{2}:\d{2}:\d{2})?'", s)
    if m:
        d = datetime.date.fromisoformat(m.group(1)) + datetime.timedelta(days=ctr)
        return "'%s%s'" % (d.isoformat(), m.group(2) or '')
    if s.startswith("'") and s.endswith("'"):
        body, suffix = s[1:-1], str(ctr)
        cap = re.search(r'\((\d+)\)', ty)
        if cap and len(body) + len(suffix) > int(cap.group(1)):
            body = body[:max(0, int(cap.group(1)) - len(suffix))]
        return "'%s%s'" % (body, suffix)
    if re.fullmatch(r'-?\d+', s):
        return str(int(s) + ctr)
    if re.fullmatch(r'-?\d+\.\d+', s):
        return '%.2f' % (float(s) + ctr)
    return s


def enforce(rows, colnames, keys, regen, fresh=None, tries=200):
    """Make a generated instance satisfy `keys`: no NULL in a key column, no repeated
    key tuple.  `fresh(col, counter, value)`, when given, supplies a value distinct by
    construction once drawing again has failed often enough.  A violating row has the
    offending key's columns drawn again from the
    same generator, so a value stays inside its join class's domain and the joins the
    query needs still meet; a row that cannot be repaired -- the key's domain is
    smaller than the number of rows -- is dropped.  Returns (rows, dropped)."""
    idx = {c: i for i, c in enumerate(colnames)}
    sets = [[idx[c] for c in k] for k in keys if all(c in idx for c in k)]
    if not sets:
        return list(rows), 0
    out, seen, ctr = [], [set() for _ in sets], 0
    for row in rows:
        row = list(row)
        for attempt in range(tries):
            bad = [cols for n, cols in enumerate(sets)
                   if any(str(row[i]).strip().upper() == 'NULL' for i in cols)
                   or tuple(row[i] for i in cols) in seen[n]]
            if not bad:
                break
            for cols in bad:
                for i in cols:
                    # the generator's own domain first: a value drawn from a join
                    # class's domain is what makes the query's joins meet, and it is
                    # worth many tries before stepping outside it
                    if fresh is None or attempt < tries // 2:
                        row[i] = regen(i)
                    else:
                        ctr += 1
                        row[i] = fresh(i, ctr, row[i])
        else:
            continue            # unrepairable: a boolean key, or no fresh value
        for n, cols in enumerate(sets):
            seen[n].add(tuple(row[i] for i in cols))
        out.append(row)
    return out, len(rows) - len(out)
