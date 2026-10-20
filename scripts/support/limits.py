"""The LIMIT values the corpus uses, where they differ from the source.

The corpus is the text ProvSQL is tested on, and that test needs a LIMIT to cut
the answer: on an instance of a few rows per table, a source's `LIMIT 100` keeps
everything. So a query whose own LIMIT is at least the number of rows it returns
has that LIMIT lowered to one less than that number. `scripts/limits.json` gives,
for each such query of the sources built by script, the source's value and the
corpus's, and `apply` makes the change.

Only the statement's final LIMIT is changed, and only where it reads exactly the
source's value, so a text that differs from the one the table was made from is
refused rather than changed somewhere else.
"""
import json
import os
import re

TAIL = re.compile(r'(?is)\blimit\s+(\d+)(\s*;?\s*)$')
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def table(path=os.path.join(HERE, 'limits.json')):
    """{scenario: {qid: [source value, corpus value]}}"""
    return json.load(open(path))


def apply(sql, source_value, corpus_value):
    """`sql` with its final `LIMIT source_value` replaced by `LIMIT corpus_value`."""
    m = TAIL.search(sql)
    if not m or int(m.group(1)) != source_value:
        raise ValueError('expected the statement to end with LIMIT %d' % source_value)
    return sql[:m.start(1)] + str(corpus_value) + m.group(2)
